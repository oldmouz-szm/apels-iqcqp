#define main worker_protocol_main
#include "../native/worker.cpp"
#undef main
#include <cassert>

static Session binary(int n=2){
    Session q;q.bounds.assign(n,{true,true,true,0,1});q.x.assign(n,0);
    q.weights={1,0};q.eps={INFINITY,INFINITY};q.objs.resize(2);return q;
}
int main(){
    {
        auto q=binary(1);q.objs[0].terms={{0,-1,-10}};q.reset();
        assert(q.s->calculate_score_no_cons(0,1)==10);
        assert(q.s->_vars[0].obj_monomials.size()==1);
    }
    {
        auto q=binary();q.weights={1,1};
        q.objs[0].terms={{0,1,5}};q.objs[1].terms={{0,1,7}};q.reset();
        assert(q.s->_object_monoials.size()==1);
        assert(q.s->calculate_obj_descent_two_vars(0,1)==12);
        q.objs[0].terms[0].a=10;q.objs[1].terms[0].a=-7;q.reset();
        assert(q.s->calculate_obj_descent_two_vars_mix(0,1,1,1)==3);
        q.objs[1].terms[0].a=-10;q.reset();
        assert(q.s->_object_monoials.empty());
        assert(q.s->_vars[0].obj_monomials.empty());
    }
    {
        auto q=binary(1);q.bounds[0]={false,true,true,.5,1.5};q.x={1};
        q.objs[0].terms={{0,-1,-1}};q.reset();Float delta=1;
        assert(!q.s->check_var_shift(0,delta,true));
        // Also test the standalone upstream guard without adapter normalization.
        auto &v=q.s->_vars[0];v.is_constant=v.equal_bound=false;v.lower=.5;v.upper=1.5;
        delta=1;assert(!q.s->check_var_shift(0,delta,true));
        double d=1;assert(!q.s->check_var_shift(0,d,true));
    }
    {
        auto q=binary(1);q.bounds[0]={false,true,true,0,5};
        q.objs[0].terms={{0,-1,-1}};q.reset();q.s->insert_operation_balance();
        assert(!q.s->_operation_value.empty());assert(q.s->_operation_value[0]>0);
        solver::polynomial_constraint c{};c.is_equal=true;c.bound=0;c.value=0;c.is_sat=true;
        assert(q.s->judge_cons_state_bin_cy_mix(&c,1,1)<0);
        c.value=1;c.is_sat=false;
        assert(q.s->judge_cons_state_bin_cy_mix(&c,-1,0)>0);
        assert(q.s->judge_cons_state_bin_cy_mix(&c,0,1)==0);
    }
    {
        auto q=binary();q.bounds[1]={false,true,true,0,2};q.x={0,1};
        q.objs[0].terms={{0,-1,-1}};q.objs[1].terms={{1,-1,1}};
        q.original={Con{Expression{{{0,-1,1},{1,-1,1}}},'=',1}};q.reset();
        auto &c=q.s->_constraints[0];assert(c.p_bin_vars.size()==1);
        assert(q.s->insert_var_change_value_balance(0,&c.var_coeff[0],0,&c,-1,false));
    }
    {
        auto q=binary(1);q.original={Con{Expression{},'>',1}};q.reset();q.configured=true;
        q.slice(.01,10);assert(q.slice_reason=="CONSTANT_CONSTRAINT_INFEASIBLE");
        q.original={Con{Expression{{{0,-1,1}}},'>',0}};q.reset();
        q.slice(.01,10);assert(q.slice_steps==0);
    }
    {
        auto q=binary(1);q.x={1};q.objs[0].terms={{0,-1,1}};
        q.original={Con{Expression{{{0,-1,1}}},'>',1}};q.reset();
        auto &c=q.s->_constraints[0];
        // A sat-preserving binary move must not turn x>=1 into x=0.
        assert(!q.s->insert_var_change_value_sat_bin(0,&c.var_coeff[0],0,&c,1,true));
        c.bound=-1;
        assert(q.s->insert_var_change_value_sat_bin(0,&c.var_coeff[0],0,&c,1,true));
        // An unchanged, unsatisfied inequality has exactly zero progress.
        c.bound=2;c.is_sat=false;
        assert(q.s->judge_cons_state_mix(&c,0,c.value)==0);
    }
    // Compare compiled scoring to direct polynomial evaluation over hundreds of
    // assignments, including repeated cross terms and opposite coefficients.
    std::mt19937 rng(17);
    for(int trial=0;trial<300;++trial){
        auto q=binary(4);q.weights={Float(1+trial%3),Float(1+trial%5)};
        for(auto &o:q.objs)for(int i=0;i<4;++i){
            o.terms.push_back({i,-1,Float(int(rng()%21)-10)});
            for(int j=i;j<4;++j)o.terms.push_back({i,j,Float(int(rng()%21)-10)});
        }
        for(auto &x:q.x)x=rng()%2;
        q.reset();
        auto value=[&](const std::vector<Float>& x){return q.weights[0]*q.eval(q.objs[0],x)+q.weights[1]*q.eval(q.objs[1],x);};
        for(int i=0;i<4;++i){auto y=q.x;y[i]=1-y[i];
            assert(q.s->calculate_score_no_cons(i,y[i]-q.x[i])==value(q.x)-value(y));
            for(int j=i+1;j<4;++j){auto z=y;z[j]=1-z[j];
                assert(q.s->calculate_obj_descent_two_vars(i,j)==value(z)-value(q.x));
                assert(q.s->calculate_obj_descent_two_vars_mix(i,z[i]-q.x[i],j,z[j]-q.x[j])==value(z)-value(q.x));
            }
        }
    }
    std::cout<<"native regression: passed\n";
}
