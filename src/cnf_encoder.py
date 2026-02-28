# compact, extensible tsl -> cnf encoder (v2)
# - supports !, &&, ||, parentheses in conditions via tseitin
# - properties are first-class vars with bi-implications to asserting options
# - error:true bans, group policy (auto exactly-one if any single:true else amo)
# - corrected t-coverage linking (a_{t,j} <-> and(options); c_t <-> or(a_{t,j}))
# - adapter pattern for other programs

from typing import Dict, List, Tuple, Any, Optional
import itertools

# ---------------- condition parser (tseitin) -----------------

class condition_parser:
    # parses identifiers incl. symbols; operators: !, &&, ||, (, )
    def __init__(self, name: str, new_var_fn):
        self.name = name
        self.new_var = new_var_fn  # returns fresh int var id

    def tokenize(self, s: str) -> List[str]:
        tokens = []
        i = 0
        while i < len(s):
            c = s[i]
            if c.isspace():
                i += 1
                continue
            if s.startswith('&&', i):
                tokens.append('&&'); i += 2; continue
            if s.startswith('||', i):
                tokens.append('||'); i += 2; continue
            if c in ('!', '(', ')'):
                tokens.append(c); i += 1; continue
            # read identifier (anything until whitespace or operator char)
            j = i
            while j < len(s) and not s[j].isspace():
                if s.startswith('&&', j) or s.startswith('||', j) or s[j] in ('!', '(', ')'):
                    break
                j += 1
            tokens.append(s[i:j])
            i = j
        return tokens

    # shunting-yard to ast
    def to_rpn(self, tokens: List[str]) -> List[str]:
        prec = {'!': 3, '&&': 2, '||': 1}
        right_assoc = {'!'}
        out = []
        op = []
        i = 0
        while i < len(tokens):
            t = tokens[i]
            if t in ('&&', '||', '!'):
                while op and op[-1] != '(' and ((prec[op[-1]] > prec[t]) or (prec[op[-1]] == prec[t] and t not in right_assoc)):
                    out.append(op.pop())
                op.append(t)
            elif t == '(':
                op.append(t)
            elif t == ')':
                while op and op[-1] != '(': out.append(op.pop())
                if not op: raise ValueError('mismatched ) in condition')
                op.pop()
            else:
                out.append(t)
            i += 1
        while op:
            if op[-1] in ('(', ')'): raise ValueError('mismatched parens in condition')
            out.append(op.pop())
        return out

    # build tseitin cnf; returns (top_var_id, clauses)
    # literals map provided by caller: get_lit(name)->(var_id, sign)
    def rpn_to_tseitin(self, rpn: List[str], get_atom_var) -> Tuple[int, List[List[int]]]:
        stk = []
        clauses: List[List[int]] = []
        for t in rpn:
            if t == '!':
                a = stk.pop()
                # z <-> ~a  ==> (z v a) (&) (~z v ~a)
                z = self.new_var()
                clauses.append([ z, a ])
                clauses.append([ -z, -a ])
                stk.append(z)
            elif t in ('&&', '||'):
                b = stk.pop(); a = stk.pop()
                z = self.new_var()
                if t == '&&':
                    # z <-> (a & b)
                    clauses.append([ -z,  a ])
                    clauses.append([ -z,  b ])
                    clauses.append([  z, -a, -b ])
                else:
                    # z <-> (a | b)
                    clauses.append([  z, -a ])
                    clauses.append([  z, -b ])
                    clauses.append([ -z,  a,  b ])
                stk.append(z)
            else:
                # atom
                v = get_atom_var(t)
                stk.append(v)
        if len(stk) != 1:
            raise ValueError('invalid condition expression')
        return stk[0], clauses

    def encode_condition(self, expr: str, get_atom_var) -> Tuple[int, List[List[int]]]:
        expr = (expr or '').strip()
        if not expr:
            # true literal via a unit var linked to true? we just return 0 to signal tautology
            return 0, []
        toks = self.tokenize(expr)
        rpn = self.to_rpn(toks)
        top, cls = self.rpn_to_tseitin(rpn, get_atom_var)
        return top, cls

# ---------------- adapters -----------------

class adapter_base:
    # implement parse() to map any program’s config to the ir used by the encoder
    def parse(self, program_config: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

class passthrough_adapter(adapter_base):
    # your current tsl json already matches the expected ir
    def parse(self, program_config: Dict[str, Any]) -> Dict[str, Any]:
        return program_config

# ---------------- encoder -----------------

class cnf_encoder:
    def __init__(self, ir: Dict[str, Any], t: int, k: int,
                 group_policy: str = 'auto',  # 'auto' | 'exactly-one' | 'at-most-one'
                 require_full_coverage: bool = True,
                 strict_conditions: bool = True,
                 antonyms: Optional[Dict[str, str]] = None):
        # config
        self.ir = ir
        self.t = t
        self.k = k
        self.group_policy = group_policy
        self.require_full_coverage = require_full_coverage
        self.strict_conditions = strict_conditions
        self.antonyms = antonyms or {}

        # cnf
        self.clauses: List[List[int]] = []

        # ids
        self.next_var = 1
        self.name_to_id: Dict[str, int] = {}
        self.id_to_name: Dict[int, str] = {}

        # base options and properties
        self.groups: Dict[str, List[str]] = {}
        self.option_to_group: Dict[str, str] = {}
        self.property_to_options: Dict[str, List[str]] = {}
        self._collect_ir()

        # per-test vars
        self.n_base = len(self.option_to_group)
        self.option_var_cache: Dict[Tuple[str, int], int] = {}
        self.property_var_cache: Dict[Tuple[str, int], int] = {}

        # coverage vars
        self.t_tuple_to_cov: Dict[Tuple[str, ...], int] = {}

    # ---- id helpers ----
    def _new_var(self, label: str) -> int:
        if label in self.name_to_id:
            return self.name_to_id[label]
        v = self.next_var
        self.next_var += 1
        self.name_to_id[label] = v
        self.id_to_name[v] = label
        return v

    def _fresh_aux(self, prefix: str) -> int:
        return self._new_var(f"{prefix}#{self.next_var}")

    def _opt_var(self, opt: str, j: int) -> int:
        key = (opt, j)
        if key not in self.option_var_cache:
            v = self._new_var(f"v:{opt}@{j}")
            self.option_var_cache[key] = v
        return self.option_var_cache[key]

    def _prop_var(self, prop: str, j: int) -> int:
        key = (prop, j)
        if key not in self.property_var_cache:
            v = self._new_var(f"p:{prop}@{j}")
            self.property_var_cache[key] = v
        return self.property_var_cache[key]

    def add(self, *lits: int):
        self.clauses.append(list(lits))

    # ---- ir collection ----
    def _collect_ir(self):
        all_groups: Dict[str, Any] = {}
        for sec in ['parameters', 'environments']:
            all_groups.update(self.ir.get(sec, {}))
        for gname, g in all_groups.items():
            names = []
            for o in g.get('options', []):
                oname = o['name']
                names.append(oname)
                self.option_to_group[oname] = gname
                prop = o.get('property')
                if prop:
                    self.property_to_options.setdefault(prop, []).append(oname)
            self.groups[gname] = names

    # ---- group constraints ----
    def _emit_group_constraints(self):
        for j in range(1, self.k + 1):
            for gname, opts in self.groups.items():
                kvars = [ self._opt_var(o, j) for o in opts ]
                policy = self.group_policy
                if policy == 'auto':
                    any_single = any(next((o2.get('single') for o2 in self._group_data(gname).get('options', []) if o2['name']==o), False) for o in opts)
                    policy = 'exactly-one' if any_single else 'at-most-one'
                if policy == 'exactly-one':
                    # alo
                    self.add(*kvars, 0)
                # amo
                for a, b in itertools.combinations(kvars, 2):
                    self.add(-a, -b, 0)
                # error bans
                for o in self._group_data(gname).get('options', []):
                    if o.get('error'):
                        self.add(-self._opt_var(o['name'], j), 0)

    def _group_data(self, gname: str) -> Dict[str, Any]:
        for sec in ['parameters', 'environments']:
            secd = self.ir.get(sec, {})
            if gname in secd: return secd[gname]
        return {}

    # ---- property bi-implications ----
    def _emit_property_links(self):
        for j in range(1, self.k + 1):
            for prop, opts in self.property_to_options.items():
                p = self._prop_var(prop, j)
                # option -> prop
                for o in opts:
                    self.add(-self._opt_var(o, j), p, 0)
                # prop -> or(options)
                self.add(-p, *(self._opt_var(o, j) for o in opts), 0)
            # antonyms exclusivity
            for a, b in self.antonym_pairs():
                pa, pb = self._prop_var(a, j), self._prop_var(b, j)
                self.add(-pa, -pb, 0)

    def antonym_pairs(self) -> List[Tuple[str, str]]:
        pairs = []
        for a, b in self.antonyms.items():
            pairs.append((a, b))
        for b, a in self.antonyms.items():
            # ensure both directions already included; skip dups
            pass
        # de-dup
        seen = set(); out = []
        for a, b in pairs:
            key = tuple(sorted((a, b)))
            if key in seen: continue
            seen.add(key); out.append((a, b))
        return out

    # ---- conditions using tseitin ----
    def _emit_conditions(self):
        parser = condition_parser('cond', self._fresh_aux)
        for j in range(1, self.k + 1):
            for gname, g in list(self.ir.get('parameters', {}).items()) + list(self.ir.get('environments', {}).items()):
                for o in g.get('options', []):
                    cond = o.get('condition')
                    if not cond: continue
                    def get_atom_var(atom_name: str) -> int:
                        # property var; allow unknown only if lenient
                        if atom_name not in self.property_to_options:
                            if self.strict_conditions:
                                raise KeyError(f"unknown property in condition: {atom_name}")
                            # create stand-alone property var
                            return self._prop_var(atom_name, j)
                        return self._prop_var(atom_name, j)
                    top, cls = parser.encode_condition(cond, get_atom_var)
                    for c in cls: self.add(*c, 0)
                    if top != 0:
                        self.add(-self._opt_var(o['name'], j), top, 0)

    # ---- coverage (corrected) ----
    def _enumerate_tuples(self):
        gnames = list(self.groups.keys())
        for grp_combo in itertools.combinations(gnames, self.t):
            lists = [ self.groups[g] for g in grp_combo ]
            for tup in itertools.product(*lists):
                key = tuple(sorted(tup))
                if key not in self.t_tuple_to_cov:
                    cid = self._new_var(f"c:{'|'.join(key)}")
                    self.t_tuple_to_cov[key] = cid

    def _emit_coverage(self):
        for key, cid in self.t_tuple_to_cov.items():
            a_js = []
            for j in range(1, self.k + 1):
                vids = [ self._opt_var(o, j) for o in key ]
                aid = self._fresh_aux('a')
                a_js.append(aid)
                # a -> each v
                for v in vids: self.add(-aid, v, 0)
                # (and v) -> a
                self.add(*(-v for v in vids), aid, 0)
            # c <-> or(a_js)
            self.add(*a_js, cid, 0)           # or(a) -> c
            self.add(-cid, *a_js, 0)           # c -> or(a)
            if self.require_full_coverage:
                self.add(cid, 0)

    # ---- public api ----
    def encode(self) -> Tuple[str, Dict[int, str]]:
        self._emit_group_constraints()
        self._emit_property_links()
        self._emit_conditions()
        self._enumerate_tuples()
        self._emit_coverage()
        nvars = self.next_var - 1
        ncls = len(self.clauses)
        header = [f"p cnf {nvars} {ncls}"]
        body = [" ".join(str(x) for x in c) for c in self.clauses]
        return "\n".join(header + body), dict(self.id_to_name)

# ---------------- example adapter usage -----------------

def build_from_program_config(program_config: Dict[str, Any], t: int, k: int,
                              antonyms: Optional[Dict[str,str]] = None,
                              group_policy: str = 'auto',
                              require_full_coverage: bool = True,
                              strict_conditions: bool = True) -> Tuple[str, Dict[int, str]]:
    # choose adapter (replace with specific adapters per tool later)
    adapter = passthrough_adapter()
    ir = adapter.parse(program_config)
    enc = cnf_encoder(ir, t, k, group_policy=group_policy,
                      require_full_coverage=require_full_coverage,
                      strict_conditions=strict_conditions,
                      antonyms=antonyms)
    return enc.encode()

# ---------------- notes -----------------
# - add new adapters by subclassing adapter_base; return the ir schema used here.
# - identifiers in conditions may include single '&' characters; only '&&' and '||' are treated as operators.
# - antonyms map helps prevent contradictory properties (e.g., {'BackUp':'NoBackUp', 'FullScan':'f&Cfoff'}).
# - set require_full_coverage=False if you want to encode tuples but not force coverage.
# - for incremental sat, build tests iteratively and reuse cnf with solver assumptions.