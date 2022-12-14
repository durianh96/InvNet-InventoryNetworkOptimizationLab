from default_paras import TERMINATION_PARM, OPT_GAP, MAX_ITER_NUM
from domain.policy import Policy
from utils.copt_pyomo import *
from utils.gsm_utils import *
from utils.utils import *


class IterativeMIP:
    def __init__(self, gsm_instance, termination_parm=TERMINATION_PARM, opt_gap=OPT_GAP, max_iter_num=MAX_ITER_NUM):
        self.gsm_instance = gsm_instance
        self.graph = gsm_instance.graph
        self.all_nodes = gsm_instance.all_nodes
        self.demand_nodes = self.graph.demand_nodes
        self.edge_list = self.graph.edge_list
        self.lt_dict = gsm_instance.lt_dict
        self.cum_lt_dict = gsm_instance.cum_lt_dict
        self.hc_dict = gsm_instance.hc_dict
        self.sla_dict = gsm_instance.sla_dict

        self.vb_func = gsm_instance.vb_func
        self.db_func = gsm_instance.db_func
        self.grad_vb_func = gsm_instance.grad_vb_func

        self.termination_parm = termination_parm
        self.opt_gap = opt_gap
        self.max_iter_num = max_iter_num

        self.need_solver = True

    @timer
    def get_policy(self, solver):
        CT_k = {j: 1.0 for j in self.all_nodes}
        a_k = {j: self.grad_vb_func[j](CT_k[j]) for j in self.all_nodes}
        f_k = {j: self.vb_func[j](CT_k[j]) - self.grad_vb_func[j](CT_k[j]) * CT_k[j] for j in self.all_nodes}
        obj_value = []
        for i in range(self.max_iter_num):
            obj_para_k = {'a_k': a_k, 'f_k': f_k}
            if solver == 'GRB':
                sol_k = self.imilp_step_grb(obj_para_k)
            elif solver == 'COPT':
                sol_k = self.imilp_step_copt(obj_para_k)
            elif solver == 'PYO_COPT':
                sol_k = self.imilp_step_pyomo(obj_para_k, pyo_solver='COPT')
            elif solver == 'PYO_GRB':
                sol_k = self.imilp_step_pyomo(obj_para_k, pyo_solver='GRB')
            elif solver == 'PYO_CBC':
                sol_k = self.imilp_step_pyomo(obj_para_k, pyo_solver='CBC')
            else:
                raise AttributeError('undefined solver')
            CT_k = sol_k['CT']
            Y_k = sol_k['Y']
            est_k = {j: a_k[j] * CT_k[j] + f_k[j] * Y_k[j] for j in self.all_nodes}
            diff_k = {j: self.hc_dict[j] * abs(est_k[j] - self.vb_func[j](CT_k[j])) for j in self.all_nodes}
            for j in self.all_nodes:
                if diff_k[j] == 0:
                    continue
                else:
                    a_k.update({j: self.grad_vb_func[j](CT_k[j])})
                    f_k.update({j: self.vb_func[j](CT_k[j]) - self.grad_vb_func[j](CT_k[j]) * CT_k[j]})
            obj_value.append(sol_k['obj_value'])
            if (i > 0) and (max(diff_k.values()) / len(self.all_nodes) <= self.termination_parm):
                break
        sol = {'S': sol_k['S'], 'SI': sol_k['SI'], 'CT': sol_k['CT']}
        error_sol = check_solution_feasibility(self.gsm_instance, sol)
        if len(error_sol) > 0:
            logger.error(error_sol)
            raise Exception
        else:
            sol['S'] = {j: round(v, 2) for j, v in sol['S'].items()}
            sol['SI'] = {j: round(v, 2) for j, v in sol['SI'].items()}
            sol['CT'] = {j: round(v, 2) for j, v in sol['CT'].items()}
            bs_dict = {node: self.db_func[node](sol['CT'][node]) for node in self.all_nodes}
            ss_dict = {node: self.vb_func[node](sol['CT'][node]) for node in self.all_nodes}
            method = 'IterativeMILP_' + solver
            cost = cal_cost(self.hc_dict, ss_dict, method=method)

            policy = Policy(self.all_nodes)
            policy.update_sol(sol)
            policy.update_base_stock(bs_dict)
            policy.update_safety_stock(ss_dict)
            policy.update_ss_cost(cost)
            return policy

    def imilp_step_grb(self, obj_para):
        import gurobipy as gp
        from gurobipy import GRB
        model = gp.Model('imilp_step')

        # adding variables
        S = model.addVars(self.all_nodes, vtype=GRB.CONTINUOUS, lb=0)
        SI = model.addVars(self.all_nodes, vtype=GRB.CONTINUOUS, lb=0)
        CT = model.addVars(self.all_nodes, vtype=GRB.CONTINUOUS, lb=0)
        Y = model.addVars(self.all_nodes, vtype=GRB.BINARY)
        M = 1e8

        # gsm constraint
        model.addConstrs((CT[j] == SI[j] + self.lt_dict[j] - S[j] for j in self.all_nodes))
        model.addConstrs((S[j] <= int(self.sla_dict[j]) for j in self.demand_nodes))

        model.addConstrs((SI[succ] - S[pred] >= 0 for (pred, succ) in self.edge_list))

        model.addConstrs((Y[j] <= M * CT[j] for j in self.all_nodes))

        model.setObjective(gp.quicksum(
            self.hc_dict[node] * (obj_para['a_k'][node] * CT[node] + obj_para['f_k'][node] * Y[node])
            for node in self.all_nodes), GRB.MINIMIZE)
        model.Params.MIPGap = self.opt_gap
        model.Params.LogToConsole = 0
        model.optimize()

        if model.status == GRB.OPTIMAL:
            sol_k = {'S': {node: float(S[node].x) for node in self.all_nodes},
                     'SI': {node: float(SI[node].x) for node in self.all_nodes},
                     'CT': {node: float(CT[node].x) for node in self.all_nodes},
                     'Y': {node: Y[node].x for node in self.all_nodes}}
            sol_k['obj_value'] = sum(
                [self.hc_dict[node] * self.vb_func[node](sol_k['CT'][node]) for node in self.all_nodes])
            return sol_k
        elif model.status == GRB.INFEASIBLE:
            raise Exception('Infeasible model')
        elif model.status == GRB.UNBOUNDED:
            raise Exception('Unbounded model')
        elif model.status == GRB.TIME_LIMIT:
            raise Exception('Time out')
        else:
            logger.error('Error status is ', model.status)
            raise Exception('Solution has not been found')

    def imilp_step_copt(self, obj_para):
        import coptpy as cp
        from coptpy import COPT
        env = cp.Envr()
        model = env.createModel('imilp_step')

        # adding variables
        S = model.addVars(self.all_nodes, vtype=COPT.CONTINUOUS, lb=0, nameprefix='S')
        SI = model.addVars(self.all_nodes, vtype=COPT.CONTINUOUS, lb=0, nameprefix='SI')
        CT = model.addVars(self.all_nodes, vtype=COPT.CONTINUOUS, lb=0, nameprefix='CT')
        Y = model.addVars(self.all_nodes, vtype=COPT.BINARY, nameprefix='Y')
        M = 1e8

        # gsm constraint
        model.addConstrs((CT[j] == SI[j] + self.lt_dict[j] - S[j] for j in self.all_nodes),
                         nameprefix='covering_time')
        model.addConstrs((S[j] <= int(self.sla_dict[j]) for j in self.demand_nodes), nameprefix='sla')

        model.addConstrs(
            (SI[succ] - S[pred] >= 0 for (pred, succ) in self.edge_list),
            nameprefix='edge')

        model.addConstrs((Y[j] <= M * CT[j] for j in self.all_nodes), nameprefix='binary')

        model.setObjective(cp.quicksum(
            self.hc_dict[node] * (obj_para['a_k'][node] * CT[node] + obj_para['f_k'][node] * Y[node])
            for node in self.all_nodes), COPT.MINIMIZE)
        model.setParam(COPT.Param.RelGap, self.opt_gap)
        model.setParam(COPT.Param.Logging, False)
        model.setParam(COPT.Param.LogToConsole, False)
        model.solve()

        if model.status == COPT.OPTIMAL:
            sol_k = {'S': {node: float(S[node].x) for node in self.all_nodes},
                     'SI': {node: float(SI[node].x) for node in self.all_nodes},
                     'CT': {node: float(CT[node].x) for node in self.all_nodes},
                     'Y': {node: Y[node].x for node in self.all_nodes}}
            sol_k['obj_value'] = sum(
                [self.hc_dict[node] * self.vb_func[node](sol_k['CT'][node]) for node in self.all_nodes])
            return sol_k
        elif model.status == COPT.INFEASIBLE:
            raise Exception('Infeasible model')
        elif model.status == COPT.UNBOUNDED:
            raise Exception("The problem is unbounded")
        else:
            raise Exception("Solution has not been found within given time limit")

    def imilp_step_pyomo(self, obj_para, pyo_solver):
        import pyomo.environ as pyo
        import pyomo.opt as pyopt
        m = pyo.ConcreteModel('imilp_step')
        # adding variables
        m.S = pyo.Var(self.all_nodes, domain=pyo.NonNegativeReals)
        m.SI = pyo.Var(self.all_nodes, domain=pyo.NonNegativeReals)
        m.CT = pyo.Var(self.all_nodes, domain=pyo.NonNegativeReals)
        m.Y = pyo.Var(self.all_nodes, domain=pyo.Binary)

        M = 1e8
        # constraints
        m.constrs = pyo.ConstraintList()
        for j in self.all_nodes:
            m.constrs.add(m.CT[j] == m.SI[j] + self.lt_dict[j] - m.S[j])
            m.constrs.add(m.Y[j] <= M * m.CT[j])

        # sla
        for j in self.demand_nodes:
            m.constrs.add(m.S[j] <= int(self.sla_dict[j]))

        # si >= s
        for pred, succ in self.edge_list:
            m.constrs.add(m.SI[succ] - m.S[pred] >= 0)

        m.Cost = pyo.Objective(
            expr=sum([self.hc_dict[j] * (obj_para['a_k'][j] * m.CT[j] + obj_para['f_k'][j] * m.Y[j])
                      for j in self.all_nodes]),
            sense=pyo.minimize
        )

        if pyo_solver == 'COPT':
            solver = pyopt.SolverFactory('copt_direct')
            solver.options['RelGap'] = self.opt_gap
        elif pyo_solver == 'GRB':
            solver = pyopt.SolverFactory('gurobi_direct')
            solver.options['MIPGap'] = self.opt_gap
        elif pyo_solver == 'CBC':
            solver = pyopt.SolverFactory('cbc')
            solver.options['ratio'] = self.opt_gap
        else:
            raise AttributeError

        solver.solve(m, tee=False)

        sol_k = {'S': {node: float(m.S[node].value) for node in self.all_nodes},
                 'SI': {node: float(m.SI[node].value) for node in self.all_nodes},
                 'CT': {node: float(m.CT[node].value) for node in self.all_nodes},
                 'Y': {node: m.Y[node].value for node in self.all_nodes}}
        sol_k['obj_value'] = sum(
            [self.hc_dict[node] * self.vb_func[node](sol_k['CT'][node]) for node in self.all_nodes])
        return sol_k

    def get_approach_paras(self):
        paras = {'termination_parm': self.termination_parm, 'opt_gap': self.opt_gap, 'max_iter_num': self.max_iter_num}
        return paras
