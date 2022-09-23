import multiprocess as mp
from domain.policy import Policy
from algorithm.base_slp import *


class SimpleSLP(BaseSLP):
    def __init__(self, gsm_instance, termination_parm=1e-4, opt_gap=0.01, max_iter_num=300, local_sol_num=20):
        super().__init__(gsm_instance, termination_parm, opt_gap, max_iter_num)
        self.local_sol_num = local_sol_num

    @timer
    def get_policy(self, solver='GRB'):
        for i in range(self.local_sol_num):
            init_CT = {j: float(random.randint(1, 150)) for j in self.all_nodes}
            self.run_one_instance(init_CT, solver)
        best_sol = self.output_results()
        self.best_sol = best_sol

        bs_dict = {node: self.db_func[node](best_sol['CT'][node]) for node in self.all_nodes}
        ss_dict = {node: self.vb_func[node](best_sol['CT'][node]) for node in self.all_nodes}
        method = 'Simple-SLP_' + solver
        cost = cal_cost(self.hc_dict, ss_dict, method=method)

        policy = Policy(self.all_nodes)
        policy.update_sol(best_sol)
        policy.update_base_stock(bs_dict)
        policy.update_safety_stock(ss_dict)
        policy.update_ss_cost(cost)
        return policy

    def get_approach_paras(self):
        paras = {'termination_parm': self.termination_parm, 'opt_gap': self.opt_gap, 'max_iter_num': self.max_iter_num,
                 'local_sol_num': self.local_sol_num, }
        return paras


class ParallelSimpleSLP(BaseSLP):
    def __init__(self, gsm_instance, graph, termination_parm=1e-4, opt_gap=0.01, max_iter_num=200, local_sol_num=20):
        super().__init__(gsm_instance, graph, termination_parm, opt_gap, max_iter_num)
        self.local_sol_num = local_sol_num

    @timer
    def get_policy(self, solver='GRB'):
        m = BaseSLP(self.gsm_instance, self.graph, self.termination_parm, self.opt_gap, self.max_iter_num)
        pool = mp.Pool(processes=8)
        sol_results = pool.map(m.get_one_instance_policy, [solver] * self.local_sol_num)
        pool.close()
        pool.join()
        best_sol = self.output_results(sol_results)
        self.best_sol = best_sol

        bs_dict = {node: self.db_func[node](best_sol['CT'][node]) for node in self.graph.all_nodes}
        ss_dict = {node: self.vb_func[node](best_sol['CT'][node]) for node in self.graph.all_nodes}
        method = 'ParallelSLP_' + solver
        cost = cal_cost(self.hc_dict, ss_dict, method=method)

        policy = Policy(self.all_nodes)
        policy.update_sol(best_sol)
        policy.update_base_stock(bs_dict)
        policy.update_safety_stock(ss_dict)
        policy.update_ss_cost(cost)
        return policy

    def get_approach_paras(self):
        paras = {'termination_parm': self.termination_parm, 'opt_gap': self.opt_gap, 'max_iter_num': self.max_iter_num,
                 'local_sol_num': self.local_sol_num, }
        return paras