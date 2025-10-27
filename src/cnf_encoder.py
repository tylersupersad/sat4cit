import sys
import itertools
from typing import Dict, List, Tuple, Any

class CNFEncoder:
    def __init__(self, config_data: Dict[str, Any], t: int, k: int):
        # the tsl json data for any program.
        self.config_data = config_data
        # the interaction strength (e.g., t=2 for pairwise).
        self.t = t
        # the size of the test suite (number of test cases).
        self.k = k
        self.cnf_clauses: List[List[int]] = []
        
        # dynamic data structures
        # maps option names to its unique base id (1 to n_base)
        self.option_to_base_id: Dict[str, int] = {}
        # maps property names to the base id of the option that asserts it
        self.property_to_base_id: Dict[str, int] = {} 
        # maps parameter/environment group name to list of base ids it contains
        self.group_options: Dict[str, List[int]] = {}
        self.n_base = 0 # total number of unique base options

        # coverage variables tracking
        # maps a sorted tuple of base ids (t-way interaction) to its coverage variable id
        self.t_tuple_to_coverage_id: Dict[Tuple[int, ...], int] = {}
        self._map_base_variables() 

        # calculate where coverage ids start
        self.coverage_var_start_id = (self.n_base * self.k) + 1
        self.next_coverage_id = self.coverage_var_start_id
        
        print(f"Encoder initialized: n_base={self.n_base}, v_min={1}, v_max_test={self.n_base * self.k}")
        print(f"Coverage variables start at: {self.coverage_var_start_id}")

    # --- core mapping functions ---

    def _map_base_variables(self):
        # dynamically generates n_base and maps all options and properties to unique base ids.
        current_id = 1
        all_groups = {}
        # combine parameters and environments for unified processing
        for section in ['parameters', 'environments']:
            all_groups.update(self.config_data.get(section, {}))

        for group_name, group_data in all_groups.items():
            base_ids = []
            for option in group_data.get('options', []):
                option_name = option['name']
                # assign the next sequential id to the option
                self.option_to_base_id[option_name] = current_id
                base_ids.append(current_id)
                
                # if an option asserts a property, map the property name to this option's id
                property_name = option.get('property')
                if property_name:
                    self.property_to_base_id[property_name] = current_id
                
                current_id += 1
            self.group_options[group_name] = base_ids
            
        self.n_base = current_id - 1
        if self.n_base == 0:
            raise ValueError("[!] Input configuration contains no options for encoding.")

    def _get_k_test_id(self, base_id: int, j: int) -> int:
        # calculates the unique variable id v_i,j for base option i in test case j.
        # v_i,j = i + (j - 1) * n_base
        return base_id + (j - 1) * self.n_base

    def _parse_condition_to_cnf(self, option_id: int, condition_str: str, j: int):
        # translates a tsl condition string (c) into cnf clauses, enforcing the implication: 
        # option -> condition (cnf: -option or condition).
        # handles simple negation (!) and conjunction (&&).
        
        # split conditions by conjunction '&&'. 
        conjuncts = [c.strip() for c in condition_str.split('&&')]
        
        # the option id that requires this condition, negated for the implication (the -a in -a or b)
        neg_option_k_id = -self._get_k_test_id(option_id, j)

        for property_term in conjuncts:
            negated = property_term.startswith('!')
            prop_name = property_term.lstrip('!').strip()
            
            # get the base id of the option that asserts the required property
            prop_base_id = self.property_to_base_id.get(prop_name)
            
            if prop_base_id is None:
                # this handles cases where a condition refers to a property that 
                # might not be explicitly defined as a property in the options list.
                # for robust research, these should be handled, but for generalization, 
                # we skip those that can't be mapped.
                print(f"[!] Warning: Property '{prop_name}' not found in property map.")
                continue

            prop_k_id = self._get_k_test_id(prop_base_id, j)
            
            if negated:
                # implication: option -> !p (cnf: -option or -p)
                self.cnf_clauses.append([neg_option_k_id, -prop_k_id, 0])
            else:
                # implication: option -> p (cnf: -option or p)
                self.cnf_clauses.append([neg_option_k_id, prop_k_id, 0])

    # duplicate psi_constraints (generalization) ---
    def _generate_constraint_duplication(self):
        # generates all structural constraints (exactly-one, conditional) for all k tests.
        
        all_groups = {}
        for section in ['parameters', 'environments']:
            all_groups.update(self.config_data.get(section, {}))
            
        for j in range(1, self.k + 1):
            
            # mutual exclusion (exactly-one) clauses: alo and amo
            for group_name in all_groups:
                base_ids = self.group_options[group_name]
                k_test_ids = [self._get_k_test_id(bid, j) for bid in base_ids]
                
                # at-least-one (alo): ensures exactly one option is chosen per group per test
                self.cnf_clauses.append(k_test_ids + [0])
                
                # at-most-one (amo): ensures no more than one option is chosen per group per test
                for id1, id2 in itertools.combinations(k_test_ids, 2):
                    self.cnf_clauses.append([-id1, -id2, 0])

            # conditional logic clauses: option -> condition
            for group_data in all_groups.values():
                for option in group_data.get('options', []):
                    if 'condition' in option:
                        option_id = self.option_to_base_id[option['name']]
                        condition_str = option['condition']
                        self._parse_condition_to_cnf(option_id, condition_str, j)

    # psi_coverage
    def _enumerate_and_map_t_tuples(self):
        # generates all unique t-way interactions across all parameter/environment groups.
        
        group_names = list(self.group_options.keys())
        
        # iterate over all unique combinations of t parameter/environment groups
        for t_groups in itertools.combinations(group_names, self.t):
            
            group_options_lists = [self.group_options[g] for g in t_groups]
            
            # cartesian product: generate all t-tuples of options from these t groups
            for t_tuple_ids in itertools.product(*group_options_lists):
                
                t_tuple_key = tuple(sorted(t_tuple_ids))
                
                if t_tuple_key not in self.t_tuple_to_coverage_id:
                    self.t_tuple_to_coverage_id[t_tuple_key] = self.next_coverage_id
                    self.next_coverage_id += 1
    
    def _generate_coverage_clauses(self):
        # generates the t-way linking (psi_link) and goal (psi_goal) clauses.
        
        for t_tuple_ids, cov_id in self.t_tuple_to_coverage_id.items():
            
            # 1. goal clause (psi_goal): c_t must be true (ensure every interaction is covered)
            self.cnf_clauses.append([cov_id, 0])
            
            # 2. linking clauses (psi_link): (v1,j and v2,j and ...) -> c_t
            # cnf: -v1,j or -v2,j or ... or c_t
            for j in range(1, self.k + 1):
                linking_clause = []
                
                for base_id in t_tuple_ids:
                    k_id = self._get_k_test_id(base_id, j)
                    linking_clause.append(-k_id) # negation of option variable
                
                linking_clause.append(cov_id) # coverage variable itself
                linking_clause.append(0) 
                
                self.cnf_clauses.append(linking_clause)

    def encode(self) -> str:
        # orchestrates the entire encoding process and returns the final dimacs cnf string.
        
        self._generate_constraint_duplication()
        self._enumerate_and_map_t_tuples()
        self._generate_coverage_clauses()
        
        total_variables = self.next_coverage_id - 1
        total_clauses = len(self.cnf_clauses)
        
        header = f"c cnf encoding (t={self.t}, k={self.k})\n"
        header += f"c program: {sys.argv[1] if len(sys.argv) > 1 else 'n/a'}\n"
        header += f"p cnf {total_variables} {total_clauses}\n"
        
        body = "\n".join([" ".join(map(str, clause)) for clause in self.cnf_clauses])
        
        return header + body