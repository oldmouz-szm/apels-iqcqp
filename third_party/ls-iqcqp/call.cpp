#include <stdio.h>
#include "sol.h"
int main(int argc,char *argv[]) // pri
{
    int flag = 2;
    if (flag == 0)
    {
        solver::qp_solver * ls_qp_solver = new solver::qp_solver;
        //读入文件和时间参数
        ls_qp_solver->read(argv[2]);
        ls_qp_solver->_cut_off = std::atof(argv[1]);
        ls_qp_solver->print_lp_formula(true);
        // ls_qp_solver->print_lp_formula(true);
        exit(0);//第一次
    }
    else if (flag == 1)
    {
        solver::qp_solver * ls_qp_solver = new solver::qp_solver;
        //读入文件和时间参数
        ls_qp_solver->read(argv[2]);
        ls_qp_solver->_cut_off = std::atof(argv[1]);
        // ls_qp_solver->seed_num = std::atoi(argv[3]);
        //读入初始解
        ls_qp_solver->read_init_solution(argv[3]); // 接收lp全文格式的初始解
        // exit(0);
        ls_qp_solver->local_search();
        // // cout << endl;
        // freopen(argv[5], "w", stdout);
        // ls_qp_solver->print_best_solution();
        // fclose(stdout);
        // ls_qp_solver->print_best_solution();
    }
    else if (flag == 2)
    {
        solver::qp_solver * ls_qp_solver = new solver::qp_solver;
        ls_qp_solver->read(argv[3]);
        ls_qp_solver->_cut_off = std::atof(argv[1]);
        // ls_qp_solver->print_formula();
        ls_qp_solver->tabu_switch = std::atoi(argv[2]);
        // cout << "tabu_switch" << ls_qp_solver->tabu_switch << endl;
        if (ls_qp_solver->tabu_switch == 1)
        {
            // cout << "tabu_switch" << endl;
            ls_qp_solver->set_constraint_tabu_enabled(true);
        }
        ls_qp_solver->local_search();
        // ls_qp_solver->print_best_solution();
    }
}

