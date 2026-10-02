// Persistent session adapter around the locally fingerprinted LS-IQCQP move engine.
#include "sol.h"
#include <sys/prctl.h>
#include <unistd.h>
#include <signal.h>
#include <memory>
#include <functional>
#include <set>
using solver::qp_solver;
using Clock=std::chrono::steady_clock;
struct Term { int i,j; Float a; }; // -1,-1 constant; i,-1 linear
struct Expression { std::vector<Term> terms; };
struct Bound {bool binary,hl,hu;Float lo,hi;};
struct Con {Expression e;char sense;Float rhs;};
struct Candidate {std::vector<Float> x;Float violation,score;int tier;uint64_t serial;};
struct Session {
    std::vector<Bound> bounds;
    std::vector<Expression> objs;
    std::vector<Con> original;
    std::vector<Float> weights,eps,x;
    std::unique_ptr<qp_solver> s;
    uint64_t seed=1,steps=0,tasks=0,recoveries=0,resets=0,warms=0;
    uint64_t slice_collect_calls=0,slice_unique=0,slice_steps=0;
    uint64_t slice_original_feasible=0,slice_original_infeasible=0,slice_replacements=0,slice_duplicates=0,candidate_serial=0;
    size_t selected_original_feasible=0,selected_repair=0;
    double slice_elapsed_ms=0,first_feasible_monotonic=-1;
    bool first_in_slice=false;
    bool bootstrap_early_return=false,bootstrap_yield_ready=false;
    std::vector<Float> last_bootstrap_export,bootstrap_export;
    std::vector<Float> first_feasible_x;
    std::string slice_reason="NOT_RUN";
    std::string branch;
    std::vector<Candidate> candidates;
    std::vector<Float> last_observed;
    bool configured=false;
    int max_expression_terms=500000;
    Clock::time_point global_deadline=Clock::time_point::max();
    Clock::time_point slice_deadline=Clock::time_point::max();
    uint64_t probes=0;
    std::string test_interrupt_phase;
    uint64_t test_interrupt_after=0;
    std::vector<std::vector<int>> assignment;
    std::vector<std::pair<int,Expression>> lifts;
    void check(const char* phase,bool in_slice=false){
        if(!test_interrupt_phase.empty()&&test_interrupt_phase==phase&&--test_interrupt_after==0)
            throw std::runtime_error("GLOBAL_DEADLINE");
        if(Clock::now()>=global_deadline)throw std::runtime_error("GLOBAL_DEADLINE");
        if(in_slice&&Clock::now()>=slice_deadline)throw std::runtime_error("SLICE_DEADLINE");
    }
    void probe(const char* phase,bool in_slice=false){if((++probes&63)==0)check(phase,in_slice);}
    bool same_assignment(const std::vector<Float>&a,const std::vector<Float>&b){
        if(a.size()!=b.size())return false;
        for(size_t i=0;i<a.size();++i){probe("collect",true);if(a[i]!=b[i])return false;}
        return true;
    }
    Float eval(const Expression&e,const std::vector<Float>&x,bool in_slice=false){
        Float v=0;check("eval",in_slice);
        for(auto&t:e.terms){probe("eval",in_slice);v+=t.a*(t.i<0?1:x[t.i])*(t.j<0?1:x[t.j]);}
        return v;
    }
    static void coeff(solver::all_coeff &a,int i,int j,Float v){
        if(j<0)a.obj_constant_coeff+=v;
        else if(i==j)a.obj_quadratic_coeff+=v;
        else{a.obj_linear_coeff.push_back(j);a.obj_linear_constant_coeff.push_back(v);}
    }
    void objective_term(Term t,Float scale){
        Float a=t.a*scale;if(!a)return;
        if(t.i<0){s->_obj_constant+=a;return;}
        if(t.j<0)s->_object_monoials.emplace_back(t.i,a,true);
        else if(t.i==t.j)s->_object_monoials.emplace_back(t.i,a,false);
        else s->_object_monoials.emplace_back(t.i,t.j,a,false);
        s->_vars_in_obj.insert(t.i);
        s->_vars[t.i].is_in_obj=true;
        if(t.j>=0){s->_vars_in_obj.insert(t.j);s->_vars[t.j].is_in_obj=true;s->is_obj_quadratic=true;}
        auto set=[&](int i,int j){auto &v=s->_vars[i];if(j<0)v.obj_constant_coeff+=a;else if(i==j)v.obj_quadratic_coeff+=a;else{v.obj_linear_coeff.push_back(j);v.obj_linear_constant_coeff.push_back(a);}};
        set(t.i,t.j);if(t.j>=0&&t.j!=t.i)set(t.j,t.i);
    }
    void constraint(const Con &c,std::string name){
        solver::polynomial_constraint p{};p.index=s->_constraints.size();p.name=name;p.bound=c.rhs;p.is_equal=c.sense=='=';p.is_less=c.sense!='>';p.value=0;p.is_sat=false;
        for(auto t:c.e.terms){
            probe("reset");
            if(!t.a)continue;
            if(t.i<0){p.bound-=t.a;continue;}
            if(t.j<0)p.monomials.emplace_back(t.i,t.a,true);
            else if(t.i==t.j)p.monomials.emplace_back(t.i,t.a,false);
            else p.monomials.emplace_back(t.i,t.j,t.a,false);
            coeff(p.var_coeff[t.i],t.i,t.j,t.a);
            s->_vars[t.i].constraints.insert(p.index);
            if(t.j>=0){p.is_quadratic=true;if(t.j!=t.i){coeff(p.var_coeff[t.j],t.j,t.i,t.a);s->_vars[t.j].constraints.insert(p.index);}}
        }
        p.is_linear=!p.is_quadratic;s->is_cons_quadratic|=p.is_quadratic;
        s->avg_bound+=fabs(p.bound);s->_constraints.push_back(p);
    }
    void reset(bool preserve_rng=false){
        try{
        check("reset");++resets;
        std::mt19937_64 rng=preserve_rng&&s?s->mo_rng:std::mt19937_64(seed);
        s=std::make_unique<qp_solver>();s->mo_rng=rng;s->mo_deadline=global_deadline;s->tabu_switch=0;s->_steps=0;s->problem_type=0;
        s->_best_steps=0;s->_object_weight=0;s->is_feasible=false;s->is_cur_feasible=false;
        for(size_t i=0;i<bounds.size();++i){
            probe("reset");
            auto b=bounds[i];s->register_var("v"+std::to_string(i));auto&v=s->_vars.back();v.is_bin=b.binary;v.is_int=true;v.is_in_obj=false;v.has_lower=b.hl;v.has_upper=b.hu;v.lower=b.lo;v.upper=b.hi;v.is_constant=b.hl&&b.hu&&b.lo==b.hi;v.constant=b.lo;v.equal_bound=v.is_constant;
            s->_int_vars.insert(i);if(b.binary)s->_bool_vars.insert(i);
        }
        for(size_t k=0;k<objs.size();++k)for(auto t:objs[k].terms){probe("reset");objective_term(t,weights[k]);}
        // Aggregate repeated objective terms (especially cancelling directions) once.
        for(size_t c=0;c<original.size();++c){probe("reset");constraint(original[c],"original:"+std::to_string(c));}
        for(size_t k=0;k<objs.size();++k)if(std::isfinite(eps[k])){probe("reset");constraint({objs[k],'<',eps[k]},"epsilon:"+std::to_string(k));}
        s->_cons_num=s->_constraints.size();s->_var_num=s->_vars.size();s->_bool_var_num=s->_bool_vars.size();s->_int_var_num=s->_int_vars.size();
        s->cons_num_type=s->_constraints.size()>50;s->judge_problem();
        if(s->_bool_vars.size()!=s->_vars.size()){branch="mix_balance";s->problem_type=s->_bool_vars.empty()?3:1;s->cons_num_type=1;s->sta_cons();}
        else if(s->_constraints.empty())branch="without_cons";
        else branch=s->is_cons_quadratic?"bin":"bin_new";
        s->_cur_assignment=x;s->_cur_delta.assign(x.size(),INT32_MIN);s->_object_weights.assign(s->_object_monoials.size(),1);
        s->_unbounded_constraints.clear();s->_unsat_constraints.clear();
        for(auto &c:s->_constraints){probe("reset");c.value=0;for(auto m:c.monomials){probe("reset");c.value+=s->pro_mono(m);}if(branch=="mix_balance")s->init_pro_con_mix(&c);else s->init_pro_con(&c);}
        s->is_cur_feasible=s->_unsat_constraints.empty();s->is_feasible=s->is_cur_feasible;s->_object_weight=s->is_cur_feasible?1:0;
        if(s->is_cur_feasible)s->update_best_solution();
        s->_start_time=Clock::now();
        s->mo_candidate=[this](){check("callback",true);collect();};
        }catch(const std::exception&e){
            if(std::string(e.what())=="DEADLINE"||Clock::now()>=global_deadline)
                throw std::runtime_error("GLOBAL_DEADLINE");
            throw;
        }
    }
    void push_recent(Candidate candidate){
        size_t count=0,oldest=candidates.size();
        for(size_t i=0;i<candidates.size();++i)if(candidates[i].tier==1){
            ++count;if(oldest==candidates.size()||candidates[i].serial<candidates[oldest].serial)oldest=i;
        }
        if(count<24)candidates.push_back(std::move(candidate));
        else{candidates[oldest]=std::move(candidate);++slice_replacements;}
    }
    void collect(){
        // Only original constraints guide priority; temporary epsilon is ignored.
        // The coordinator remains the authoritative exact original-model validator.
        ++slice_collect_calls;
        check("collect",true);
        const auto &y=s->_cur_assignment;
        if(same_assignment(y,last_observed)){++slice_duplicates;return;}
        last_observed=y;
        for(const auto &c:candidates){probe("collect",true);if(same_assignment(c.x,y)){++slice_duplicates;return;}}
        ++slice_unique;
        Float violation=0;bool feasible=true;
        for(size_t i=0;i<bounds.size();++i){
            probe("collect",true);
            const auto &b=bounds[i];Float delta=0;
            if(floor(y[i])!=y[i])delta=1;
            if(b.hl&&y[i]<b.lo)delta+=b.lo-y[i];
            if(b.hu&&y[i]>b.hi)delta+=y[i]-b.hi;
            if(delta>0){feasible=false;violation+=delta;}
        }
        for(const auto &c:original){
            probe("collect",true);
            Float delta=eval(c.e,y,true)-c.rhs;
            Float error=c.sense=='='?fabs(delta):c.sense=='<'?std::max(Float(0),delta):std::max(Float(0),-delta);
            if(error>0){feasible=false;violation+=error/(1+fabs(c.rhs));}
        }
        if(!std::isfinite(violation)){feasible=false;violation=std::numeric_limits<Float>::infinity();}
        if(feasible){
            ++slice_original_feasible;
            if(first_feasible_monotonic<0){first_feasible_monotonic=std::chrono::duration<double>(Clock::now().time_since_epoch()).count();first_feasible_x=y;first_in_slice=true;}
            Float score=0;for(size_t k=0;k<objs.size();++k){probe("collect",true);score+=weights[k]*eval(objs[k],y,true);}
            if(!std::isfinite(score))score=std::numeric_limits<Float>::infinity();
            Candidate candidate{y,0,score,0,++candidate_serial};
            size_t best_count=0,worst=candidates.size();
            for(size_t i=0;i<candidates.size();++i)if(candidates[i].tier==0){probe("collect",true);
                ++best_count;if(worst==candidates.size()||candidates[i].score>candidates[worst].score)worst=i;
            }
            if(best_count<24)candidates.push_back(std::move(candidate));
            else if(score<candidates[worst].score){
                Candidate demoted=std::move(candidates[worst]);demoted.tier=1;
                candidates[worst]=std::move(candidate);push_recent(std::move(demoted));++slice_replacements;
            }else{candidate.tier=1;push_recent(std::move(candidate));}
            // Do not interrupt a compound LS move. The slice loop observes this
            // only at its initial safe state or after a whole step completes.
            if(bootstrap_early_return&&!bootstrap_yield_ready&&!same_assignment(y,last_bootstrap_export)){
                bootstrap_export=y;bootstrap_yield_ready=true;
            }
        }else{
            ++slice_original_infeasible;
            size_t count=0,worst=candidates.size();
            for(size_t i=0;i<candidates.size();++i)if(candidates[i].tier==2){probe("collect",true);
                ++count;if(worst==candidates.size()||candidates[i].violation>candidates[worst].violation)worst=i;
            }
            Candidate candidate{y,violation,0,2,++candidate_serial};
            if(count<16)candidates.push_back(std::move(candidate));
            else if(violation<candidates[worst].violation){candidates[worst]=std::move(candidate);++slice_replacements;}
        }
    }
    void slice(double seconds,uint64_t limit,bool early_return=false){
        if(!configured)throw std::runtime_error("TASK_REQUIRED");
        check("slice");
        auto begin=Clock::now();bootstrap_early_return=early_return;bootstrap_yield_ready=false;first_in_slice=false;slice_collect_calls=0;slice_unique=0;slice_steps=0;slice_reason="UNKNOWN";
        slice_original_feasible=0;slice_original_infeasible=0;slice_replacements=0;slice_duplicates=0;
        last_observed.clear();
        auto end=Clock::now()+std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(seconds));
        slice_deadline=end;s->mo_deadline=std::min(end,global_deadline);
        try{collect();}catch(const std::exception&e){
            if(std::string(e.what())=="GLOBAL_DEADLINE")throw;
            if(std::string(e.what())!="SLICE_DEADLINE")throw;
            slice_reason="DEADLINE_PROBE";
        }
        for(uint64_t i=0;i<limit&&Clock::now()<end&&!bootstrap_yield_ready;++i){
            auto safe=s->_cur_assignment;
            try{
                if(s->_object_monoials.empty()&&s->_constraints.empty()){slice_reason="NO_SEARCH_TERMS";break;}
                if(branch=="without_cons")s->mo_step_without_cons();
                else if(branch=="bin")s->mo_step_bin();
                else if(branch=="bin_new")s->mo_step_bin_new();
                else s->mo_step_mix_balance();
                ++steps;++slice_steps;collect();x=s->_cur_assignment;
            }catch(const std::exception&e){
                const std::string reason=e.what();
                if(reason=="GLOBAL_DEADLINE"||Clock::now()>=global_deadline)throw std::runtime_error("GLOBAL_DEADLINE");
                bootstrap_yield_ready=false;x=safe;auto rng=s->mo_rng;
                reset(true);s->mo_rng=rng;++recoveries;
                if(reason!="DEADLINE"&&reason!="SLICE_DEADLINE")throw;
                slice_reason="DEADLINE_PROBE";
                break;
            }
        }
        if(bootstrap_yield_ready){
            last_bootstrap_export=bootstrap_export;slice_reason="FIRST_FEASIBLE_CANDIDATE";
        }
        if(slice_reason=="UNKNOWN")slice_reason=slice_steps>=limit?"STEP_LIMIT":Clock::now()>=end?"TIME_LIMIT":"SEARCH_STOP";
        selected_original_feasible=0;selected_repair=0;
        for(const auto &candidate:candidates){if(candidate.tier==2)++selected_repair;else ++selected_original_feasible;}
        slice_elapsed_ms=std::chrono::duration<double,std::milli>(Clock::now()-begin).count();
        s->mo_deadline=Clock::time_point::max();slice_deadline=Clock::time_point::max();x=s->_cur_assignment;
    }
};
Expression read_expression(Session&q,int max_terms){Expression e;int n;std::cin>>n;
    q.check("load");
    if(n<0||n>max_terms||n>1000000)throw std::runtime_error("TERM_LIMIT");
    for(int t=0;t<n;++t){q.probe("load");Term m;std::cin>>m.i>>m.j>>m.a;
        if(!std::isfinite(m.a))throw std::runtime_error("NONFINITE");e.terms.push_back(m);}
    return e;
}
void emit_x(const std::vector<Float>&x){std::cout<<'[';for(size_t i=0;i<x.size();++i){if(i)std::cout<<',';std::cout<<std::setprecision(21)<<x[i];}std::cout<<']';}
int main(){
    // Parent PID comes from the owning Python process; no system settings change.
    if(const char* expected=std::getenv("MO_IQCQP_EXPECTED_PARENT")){
        const pid_t parent=static_cast<pid_t>(std::strtol(expected,nullptr,10));
        if(prctl(PR_SET_PDEATHSIG,SIGKILL)!=0)return 125;
        if(getppid()!=parent)return 125;
    }
    Session q;std::string cmd;
    while(std::cin>>cmd){try{
        if(cmd=="GUARD"){
            double seconds;std::cin>>seconds;
            if(!std::isfinite(seconds)||seconds<=0)throw std::runtime_error("INVALID_GUARD");
            q.global_deadline=Clock::now()+std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(seconds));
            std::cout<<"{\"status\":\"GUARDED\"}\n";
        }else if(cmd=="TEST_INTERRUPT"){
            const char *enabled=std::getenv("MO_IQCQP_TEST_HOOKS");
            if(!enabled||std::string(enabled)!="1")throw std::runtime_error("TEST_HOOK_DISABLED");
            std::cin>>q.test_interrupt_phase>>q.test_interrupt_after;
            if(q.test_interrupt_after==0)throw std::runtime_error("INVALID_TEST_INTERRUPT");
            std::cout<<"{\"status\":\"TEST_INTERRUPT_SET\"}\n";
        }else if(cmd=="LOAD"){
            if(!q.bounds.empty())throw std::runtime_error("MODEL_ALREADY_LOADED");
            int n,m,c,max_n,max_c,max_total,max_expr;
            if(!(std::cin>>n>>m>>c>>max_n>>max_c>>max_total>>max_expr))throw std::runtime_error("INVALID_LOAD_HEADER");
            if(max_n<1||max_n>100000||max_c<1||max_c>100000||max_total<1||max_total>20000000||max_expr<1||max_expr>1000000||max_expr>max_total)
                throw std::runtime_error("CONFIGURED_LIMIT_INVALID");
            if(n<1||n>max_n||m<2||c<0||c>max_c)throw std::runtime_error("DIMENSION_LIMIT");
            q.max_expression_terms=max_expr;
            for(int i=0;i<n;++i){q.probe("load");Bound b;std::cin>>b.binary>>b.hl>>b.lo>>b.hu>>b.hi;q.bounds.push_back(b);Float y=0;if(b.hl)y=std::max(y,ceil(b.lo));if(b.hu)y=std::min(y,floor(b.hi));q.x.push_back(y);}
            uint64_t total=0;
            for(int k=0;k<m;++k){auto e=read_expression(q,max_expr);total+=e.terms.size();if(total>max_total)throw std::runtime_error("TOTAL_TERM_LIMIT");q.objs.push_back(std::move(e));}
            for(int j=0;j<c;++j){q.probe("load");Con a;std::cin>>a.sense>>a.rhs;a.e=read_expression(q,max_expr);total+=a.e.terms.size();if(total>max_total)throw std::runtime_error("TOTAL_TERM_LIMIT");q.original.push_back(std::move(a));}
            q.weights.assign(m,1);q.eps.assign(m,std::numeric_limits<Float>::infinity());std::cout<<"{\"status\":\"LOADED\"}\n";
        }else if(cmd=="SEED"){std::cin>>q.seed;if(q.s)q.s->mo_seed(q.seed);std::cout<<"{\"status\":\"SEEDED\"}\n";
        }else if(cmd=="TASK"){
            q.check("reset");
            for(auto&w:q.weights)std::cin>>w;
            for(auto&e:q.eps){int on;Float v;std::cin>>on>>v;e=on?v:std::numeric_limits<Float>::infinity();}
            q.reset(true);q.configured=true;++q.tasks;q.candidates.clear();q.last_bootstrap_export.clear();std::cout<<"{\"status\":\"TASK_SET\",\"branch\":\""<<q.branch<<"\"}\n";
        }else if(cmd=="WARM"){
            q.check("reset");++q.warms;
            for(size_t i=0;i<q.x.size();++i){q.probe("reset");Float y;std::cin>>y;auto b=q.bounds[i];if(!std::isfinite(y)||floor(y)!=y||(b.hl&&y<b.lo)||(b.hu&&y>b.hi))throw std::runtime_error("INVALID_WARM_START");q.x[i]=y;}
            if(q.configured)q.reset(true);q.candidates.clear();q.last_bootstrap_export.clear();std::cout<<"{\"status\":\"WARM_STARTED\"}\n";
        }else if(cmd=="SLICE"||cmd=="BOOTSTRAP_SLICE"){
            double sec;uint64_t limit;std::cin>>sec>>limit;q.slice(sec,limit,cmd=="BOOTSTRAP_SLICE");std::cout<<"{\"status\":\"SLICE_DONE\",\"steps\":"<<q.steps<<",\"slice_steps\":"<<q.slice_steps<<",\"collect_calls\":"<<q.slice_collect_calls<<",\"unique_collected\":"<<q.slice_unique<<",\"duplicate_observations\":"<<q.slice_duplicates<<",\"observed_original_feasible\":"<<q.slice_original_feasible<<",\"observed_original_infeasible\":"<<q.slice_original_infeasible<<",\"selected_original_feasible\":"<<q.selected_original_feasible<<",\"selected_repair\":"<<q.selected_repair<<",\"selection_replacements\":"<<q.slice_replacements<<",\"candidate_count\":"<<q.candidates.size()<<",\"stop_reason\":\""<<q.slice_reason<<"\",\"slice_elapsed_ms\":"<<q.slice_elapsed_ms<<",\"task_requests\":"<<q.tasks<<",\"warm_requests\":"<<q.warms<<",\"reset_count\":"<<q.resets<<",\"deadline_recoveries\":"<<q.recoveries<<",\"first_original_feasible_monotonic\":";if(q.first_feasible_monotonic<0)std::cout<<"null";else std::cout<<std::setprecision(17)<<q.first_feasible_monotonic;std::cout<<",\"first_original_feasible_x\":";if(q.first_in_slice)emit_x(q.first_feasible_x);else std::cout<<"null";std::cout<<"}\n";
        }else if(cmd=="COLLECT"){
            std::cout<<"{\"status\":\"CANDIDATES\",\"x\":[";for(size_t i=0;i<q.candidates.size();++i){if(i)std::cout<<',';emit_x(q.candidates[i].x);}std::cout<<"]}\n";q.candidates.clear();
        }else if(cmd=="STRUCTURE"){
            q.check("load");
            int n,l;std::cin>>n>>l;q.assignment.assign(n,std::vector<int>(n));
            for(auto &row:q.assignment)for(auto &j:row){q.probe("load");std::cin>>j;}
            for(int k=0;k<l;++k){q.probe("load");int j;std::cin>>j;q.lifts.push_back({j,read_expression(q,q.max_expression_terms)});}
            std::cout<<"{\"status\":\"STRUCTURE_SET\"}\n";
        }else if(cmd=="NEIGHBORS"){
            double sec;size_t limit,offset;std::cin>>sec>>limit>>offset;
            auto end=Clock::now()+std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(sec));
            q.candidates.clear();auto base=q.x;
            if(q.assignment.empty())limit=std::min(limit,2*base.size());
            for(size_t k=0;k<limit&&Clock::now()<end;++k){q.probe("neighbors");
                if(!q.assignment.empty()){
                    size_t n=q.assignment.size(),a=(k+offset)%n,b=(a+1+(k+offset)/n%(n-1))%n;
                    auto candidate=base;
                    for(size_t col=0;col<n;++col)std::swap(candidate[q.assignment[a][col]],candidate[q.assignment[b][col]]);
                    if(n>2&&k%3==0){size_t c=(b+1)%n;if(c==a)c=(c+1)%n;for(size_t col=0;col<n;++col)std::swap(candidate[q.assignment[b][col]],candidate[q.assignment[c][col]]);}
                    for(auto &lift:q.lifts)candidate[lift.first]=q.eval(lift.second,candidate);
                    q.candidates.push_back({std::move(candidate),0,0,1,++q.candidate_serial});continue;
                }
                size_t op=(k+offset)%(2*base.size());size_t i=op/2;Float y=base[i]+(op%2?-1:1);auto b=q.bounds[i];
                if((b.hl&&y<b.lo)||(b.hu&&y>b.hi))continue;
                auto candidate=base;candidate[i]=y;q.candidates.push_back({std::move(candidate),0,0,1,++q.candidate_serial});
            }
            std::cout<<"{\"status\":\"NEIGHBORS_DONE\",\"count\":"<<q.candidates.size()<<"}\n";
        }else if(cmd=="STATS")std::cout<<"{\"status\":\"OK\",\"steps\":"<<q.steps<<",\"tasks\":"<<q.tasks<<",\"deadline_recoveries\":"<<q.recoveries<<"}\n";
        else if(cmd=="MOVE"){
            int i;Float delta;std::cin>>i>>delta;
            if(i<0||i>=int(q.x.size()))throw std::runtime_error("MOVE_INDEX");
            Float before=q.s->_cur_assignment[i];Float objective_delta=q.s->pro_var_value_delta_in_obj_cy(&q.s->_vars[i],i,before,before+delta);
            if(q.branch=="without_cons")q.s->execute_critical_move_no_cons(i,delta);else if(q.branch=="mix_balance")q.s->execute_critical_move_mix(i,delta);else q.s->execute_critical_move(i,delta);
            q.x=q.s->_cur_assignment;std::cout<<"{\"status\":\"MOVED\",\"x\":";emit_x(q.x);std::cout<<",\"constraints\":[";
            for(size_t j=0;j<q.s->_constraints.size();++j){if(j)std::cout<<',';std::cout<<q.s->_constraints[j].value;}
            std::cout<<"],\"objective_delta\":"<<objective_delta<<"}\n";
        }else if(cmd=="CLOSE"){std::cout<<"{\"status\":\"CLOSED\"}\n";break;}
        else throw std::runtime_error("UNKNOWN_COMMAND");
        if(!std::cin)throw std::runtime_error("PROTOCOL_PARSE_ERROR");
        std::cout.flush();
    }catch(const std::exception &e){std::cout<<"{\"status\":\"ERROR\",\"error\":\""<<e.what()<<"\"}\n"<<std::flush;return 2;}}
}
