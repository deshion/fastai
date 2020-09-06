# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/40_tabular.core.ipynb (unless otherwise specified).

__all__ = ['make_date', 'add_datepart', 'add_elapsed_times', 'cont_cat_split', 'df_shrink_dtypes', 'df_shrink',
           'Tabular', 'TabularPandas', 'TabularProc', 'Categorify', 'setups', 'encodes', 'decodes', 'setups', 'encodes',
           'decodes', 'FillStrategy', 'FillMissing', 'ReadTabBatch', 'TabDataLoader', 'setups', 'encodes', 'decodes',
           'setups', 'encodes', 'decodes']

# Cell
from ..torch_basics import *
from ..data.all import *

# Cell
pd.set_option('mode.chained_assignment','raise')

# Cell
def make_date(df, date_field):
    "Make sure `df[date_field]` is of the right date type."
    field_dtype = df[date_field].dtype
    if isinstance(field_dtype, pd.core.dtypes.dtypes.DatetimeTZDtype):
        field_dtype = np.datetime64
    if not np.issubdtype(field_dtype, np.datetime64):
        df[date_field] = pd.to_datetime(df[date_field], infer_datetime_format=True)

# Cell
def add_datepart(df, field_name, prefix=None, drop=True, time=False):
    "Helper function that adds columns relevant to a date in the column `field_name` of `df`."
    make_date(df, field_name)
    field = df[field_name]
    prefix = ifnone(prefix, re.sub('[Dd]ate$', '', field_name))
    attr = ['Year', 'Month', 'Day', 'Dayofweek', 'Dayofyear', 'Is_month_end', 'Is_month_start',
            'Is_quarter_end', 'Is_quarter_start', 'Is_year_end', 'Is_year_start']
    if time: attr = attr + ['Hour', 'Minute', 'Second']
    for n in attr: df[prefix + n] = getattr(field.dt, n.lower())
    # Pandas removed `dt.week` in v1.1.10
    week = field.dt.isocalendar().week if hasattr(field.dt, 'isocalendar') else field.dt.week
    df.insert(3, prefix+'Week', week)
    mask = ~field.isna()
    df[prefix + 'Elapsed'] = np.where(mask,field.values.astype(np.int64) // 10 ** 9,None)
    if drop: df.drop(field_name, axis=1, inplace=True)
    return df

# Cell
def _get_elapsed(df,field_names, date_field, base_field, prefix):
    for f in field_names:
        day1 = np.timedelta64(1, 'D')
        last_date,last_base,res = np.datetime64(),None,[]
        for b,v,d in zip(df[base_field].values, df[f].values, df[date_field].values):
            if last_base is None or b != last_base:
                last_date,last_base = np.datetime64(),b
            if v: last_date = d
            res.append(((d-last_date).astype('timedelta64[D]') / day1))
        df[prefix + f] = res
    return df

# Cell
def add_elapsed_times(df, field_names, date_field, base_field):
    "Add in `df` for each event in `field_names` the elapsed time according to `date_field` grouped by `base_field`"
    field_names = list(L(field_names))
    #Make sure date_field is a date and base_field a bool
    df[field_names] = df[field_names].astype('bool')
    make_date(df, date_field)

    work_df = df[field_names + [date_field, base_field]]
    work_df = work_df.sort_values([base_field, date_field])
    work_df = _get_elapsed(work_df, field_names, date_field, base_field, 'After')
    work_df = work_df.sort_values([base_field, date_field], ascending=[True, False])
    work_df = _get_elapsed(work_df, field_names, date_field, base_field, 'Before')

    for a in ['After' + f for f in field_names] + ['Before' + f for f in field_names]:
        work_df[a] = work_df[a].fillna(0).astype(int)

    for a,s in zip([True, False], ['_bw', '_fw']):
        work_df = work_df.set_index(date_field)
        tmp = (work_df[[base_field] + field_names].sort_index(ascending=a)
                      .groupby(base_field).rolling(7, min_periods=1).sum())
        tmp.drop(base_field,1,inplace=True)
        tmp.reset_index(inplace=True)
        work_df.reset_index(inplace=True)
        work_df = work_df.merge(tmp, 'left', [date_field, base_field], suffixes=['', s])
    work_df.drop(field_names,1,inplace=True)
    return df.merge(work_df, 'left', [date_field, base_field])

# Cell
def cont_cat_split(df, max_card=20, dep_var=None):
    "Helper function that returns column names of cont and cat variables from given `df`."
    cont_names, cat_names = [], []
    for label in df:
        if label == dep_var: continue
        if df[label].dtype == int and df[label].unique().shape[0] > max_card or df[label].dtype == float:
            cont_names.append(label)
        else: cat_names.append(label)
    return cont_names, cat_names

# Cell
def df_shrink_dtypes(df, skip=[], obj2cat=True, int2uint=False):
    "Return any possible smaller data types for DataFrame columns. Allows `object`->`category`, `int`->`uint`, and exclusion."

    # 1: Build column filter and typemap
    excl_types, skip = {'category','datetime64[ns]','bool'}, set(skip)

    typemap = {'int'   : [(np.dtype(x), np.iinfo(x).min, np.iinfo(x).max) for x in (np.int8, np.int16, np.int32, np.int64)],
               'uint'  : [(np.dtype(x), np.iinfo(x).min, np.iinfo(x).max) for x in (np.uint8, np.uint16, np.uint32, np.uint64)],
               'float' : [(np.dtype(x), np.finfo(x).min, np.finfo(x).max) for x in (np.float32, np.float64, np.longdouble)]
              }
    if obj2cat: typemap['object'] = 'category'  # User wants to categorify dtype('Object'), which may not always save space
    else:       excl_types.add('object')

    new_dtypes = {}
    exclude = lambda dt: dt[1].name not in excl_types and dt[0] not in skip

    for c, old_t in filter(exclude, df.dtypes.items()):
        t = next((v for k,v in typemap.items() if old_t.name.startswith(k)), None)

        if isinstance(t, list): # Find the smallest type that fits
            if int2uint and t==typemap['int'] and df[c].min() >= 0: t=typemap['uint']
            new_t = next((r[0] for r in t if r[1]<=df[c].min() and r[2]>=df[c].max()), None)
            if new_t and new_t == old_t: new_t = None
        else: new_t = t if isinstance(t, str) else None

        if new_t: new_dtypes[c] = new_t
    return new_dtypes

# Cell
def df_shrink(df, skip=[], obj2cat=True, int2uint=False):
    "Reduce DataFrame memory usage, by casting to smaller types returned by `df_shrink_dtypes()`."
    dt = df_shrink_dtypes(df, skip, obj2cat=obj2cat, int2uint=int2uint)
    return df.astype(dt)

# Cell
class _TabIloc:
    "Get/set rows by iloc and cols by name"
    def __init__(self,to): self.to = to
    def __getitem__(self, idxs):
        df = self.to.items
        if isinstance(idxs,tuple):
            rows,cols = idxs
            cols = df.columns.isin(cols) if is_listy(cols) else df.columns.get_loc(cols)
        else: rows,cols = idxs,slice(None)
        return self.to.new(df.iloc[rows, cols])

# Cell
class Tabular(CollBase, GetAttr, FilteredBase):
    "A `DataFrame` wrapper that knows which cols are cont/cat/y, and returns rows in `__getitem__`"
    _default,with_cont='procs',True
    def __init__(self, df, procs=None, cat_names=None, cont_names=None, y_names=None, y_block=None, splits=None,
                 do_setup=True, device=None, inplace=False, reduce_memory=True):
        if inplace and splits is not None and pd.options.mode.chained_assignment is not None:
            warn("Using inplace with splits will trigger a pandas error. Set `pd.options.mode.chained_assignment=None` to avoid it.")
        if not inplace: df = df.copy()
        if reduce_memory: df = df_shrink(df)
        if splits is not None: df = df.iloc[sum(splits, [])]
        self.dataloaders = delegates(self._dl_type.__init__)(self.dataloaders)
        super().__init__(df)

        self.y_names,self.device = L(y_names),device
        if y_block is None and self.y_names:
            # Make ys categorical if they're not numeric
            ys = df[self.y_names]
            if len(ys.select_dtypes(include='number').columns)!=len(ys.columns): y_block = CategoryBlock()
            else: y_block = RegressionBlock()
        if y_block is not None and do_setup:
            if callable(y_block): y_block = y_block()
            procs = L(procs) + y_block.type_tfms
        self.cat_names,self.cont_names,self.procs = L(cat_names),L(cont_names),Pipeline(procs)
        self.split = len(df) if splits is None else len(splits[0])
        if do_setup: self.setup()

    def new(self, df):
        return type(self)(df, do_setup=False, reduce_memory=False, y_block=TransformBlock(),
                          **attrdict(self, 'procs','cat_names','cont_names','y_names', 'device'))

    def subset(self, i): return self.new(self.items[slice(0,self.split) if i==0 else slice(self.split,len(self))])
    def copy(self): self.items = self.items.copy(); return self
    def decode(self): return self.procs.decode(self)
    def decode_row(self, row): return self.new(pd.DataFrame(row).T).decode().items.iloc[0]
    def show(self, max_n=10, **kwargs): display_df(self.new(self.all_cols[:max_n]).decode().items)
    def setup(self): self.procs.setup(self)
    def process(self): self.procs(self)
    def loc(self): return self.items.loc
    def iloc(self): return _TabIloc(self)
    def targ(self): return self.items[self.y_names]
    def x_names (self): return self.cat_names + self.cont_names
    def n_subsets(self): return 2
    def y(self): return self[self.y_names[0]]
    def new_empty(self): return self.new(pd.DataFrame({}, columns=self.items.columns))
    def to_device(self, d=None):
        self.device = d
        return self

    def all_col_names (self):
        ys = [n for n in self.y_names if n in self.items.columns]
        return self.x_names + self.y_names if len(ys) == len(self.y_names) else self.x_names

properties(Tabular,'loc','iloc','targ','all_col_names','n_subsets','x_names','y')

# Cell
class TabularPandas(Tabular):
    "A `Tabular` object with transforms"
    def transform(self, cols, f, all_col=True):
        if not all_col: cols = [c for c in cols if c in self.items.columns]
        if len(cols) > 0: self[cols] = self[cols].transform(f)

# Cell
def _add_prop(cls, nm):
    @property
    def f(o): return o[list(getattr(o,nm+'_names'))]
    @f.setter
    def fset(o, v): o[getattr(o,nm+'_names')] = v
    setattr(cls, nm+'s', f)
    setattr(cls, nm+'s', fset)

_add_prop(Tabular, 'cat')
_add_prop(Tabular, 'cont')
_add_prop(Tabular, 'y')
_add_prop(Tabular, 'x')
_add_prop(Tabular, 'all_col')

# Cell
class TabularProc(InplaceTransform):
    "Base class to write a non-lazy tabular processor for dataframes"
    def setup(self, items=None, train_setup=False): #TODO: properly deal with train_setup
        super().setup(getattr(items,'train',items), train_setup=False)
        # Procs are called as soon as data is available
        return self(items.items if isinstance(items,Datasets) else items)

    @property
    def name(self): return f"{super().name} -- {getattr(self,'__stored_args__',{})}"

# Cell
def _apply_cats (voc, add, c):
    if not is_categorical_dtype(c):
        return pd.Categorical(c, categories=voc[c.name][add:]).codes+add
    return c.cat.codes+add #if is_categorical_dtype(c) else c.map(voc[c.name].o2i)
def _decode_cats(voc, c): return c.map(dict(enumerate(voc[c.name].items)))

# Cell
class Categorify(TabularProc):
    "Transform the categorical variables to something similar to `pd.Categorical`"
    order = 1
    def setups(self, to):
        store_attr(classes={n:CategoryMap(to.iloc[:,n].items, add_na=(n in to.cat_names)) for n in to.cat_names})

    def encodes(self, to): to.transform(to.cat_names, partial(_apply_cats, self.classes, 1))
    def decodes(self, to): to.transform(to.cat_names, partial(_decode_cats, self.classes))
    def __getitem__(self,k): return self.classes[k]

# Cell
@Categorize
def setups(self, to:Tabular):
    if len(to.y_names) > 0:
        if self.vocab is None:
            self.vocab = CategoryMap(getattr(to, 'train', to).iloc[:,to.y_names[0]].items, strict=True)
        else:
            self.vocab = CategoryMap(self.vocab, sort=False, add_na=self.add_na)
        self.c = len(self.vocab)
    return self(to)

@Categorize
def encodes(self, to:Tabular):
    to.transform(to.y_names, partial(_apply_cats, {n: self.vocab for n in to.y_names}, 0), all_col=False)
    return to

@Categorize
def decodes(self, to:Tabular):
    to.transform(to.y_names, partial(_decode_cats, {n: self.vocab for n in to.y_names}), all_col=False)
    return to

# Cell
@Normalize
def setups(self, to:Tabular):
    store_attr(means=dict(getattr(to, 'train', to).conts.mean()),
               stds=dict(getattr(to, 'train', to).conts.std(ddof=0)+1e-7))
    return self(to)

@Normalize
def encodes(self, to:Tabular):
    to.conts = (to.conts-self.means) / self.stds
    return to

@Normalize
def decodes(self, to:Tabular):
    to.conts = (to.conts*self.stds ) + self.means
    return to

# Cell
class FillStrategy:
    "Namespace containing the various filling strategies."
    def median  (c,fill): return c.median()
    def constant(c,fill): return fill
    def mode    (c,fill): return c.dropna().value_counts().idxmax()

# Cell
class FillMissing(TabularProc):
    "Fill the missing values in continuous columns."
    def __init__(self, fill_strategy=FillStrategy.median, add_col=True, fill_vals=None):
        if fill_vals is None: fill_vals = defaultdict(int)
        store_attr()

    def setups(self, dsets):
        missing = pd.isnull(dsets.conts).any()
        store_attr(na_dict={n:self.fill_strategy(dsets[n], self.fill_vals[n])
                            for n in missing[missing].keys()})
        self.fill_strategy = self.fill_strategy.__name__

    def encodes(self, to):
        missing = pd.isnull(to.conts)
        for n in missing.any()[missing.any()].keys():
            assert n in self.na_dict, f"nan values in `{n}` but not in setup training set"
        for n in self.na_dict.keys():
            to[n].fillna(self.na_dict[n], inplace=True)
            if self.add_col:
                to.loc[:,n+'_na'] = missing[n]
                if n+'_na' not in to.cat_names: to.cat_names.append(n+'_na')

# Cell
def _maybe_expand(o): return o[:,None] if o.ndim==1 else o

# Cell
class ReadTabBatch(ItemTransform):
    def __init__(self, to): self.to = to

    def encodes(self, to):
        if not to.with_cont: res = (tensor(to.cats).long(),)
        else: res = (tensor(to.cats).long(),tensor(to.conts).float())
        ys = [n for n in to.y_names if n in to.items.columns]
        if len(ys) == len(to.y_names): res = res + (tensor(to.targ),)
        if to.device is not None: res = to_device(res, to.device)
        return res

    def decodes(self, o):
        o = [_maybe_expand(o_) for o_ in to_np(o) if o_.size != 0]
        vals = np.concatenate(o, axis=1)
        try: df = pd.DataFrame(vals, columns=self.to.all_col_names)
        except: df = pd.DataFrame(vals, columns=self.to.x_names)
        to = self.to.new(df)
        return to

# Cell
@typedispatch
def show_batch(x: Tabular, y, its, max_n=10, ctxs=None):
    x.show()

# Cell
@delegates()
class TabDataLoader(TfmdDL):
    "A transformed `DataLoader` for Tabular data"
    do_item = noops
    def __init__(self, dataset, bs=16, shuffle=False, after_batch=None, num_workers=0, **kwargs):
        if after_batch is None: after_batch = L(TransformBlock().batch_tfms)+ReadTabBatch(dataset)
        super().__init__(dataset, bs=bs, shuffle=shuffle, after_batch=after_batch, num_workers=num_workers, **kwargs)

    def create_batch(self, b): return self.dataset.iloc[b]

TabularPandas._dl_type = TabDataLoader

# Cell
@EncodedMultiCategorize
def setups(self, to:Tabular):
    self.c = len(self.vocab)
    return self(to)

@EncodedMultiCategorize
def encodes(self, to:Tabular): return to

@EncodedMultiCategorize
def decodes(self, to:Tabular):
    to.transform(to.y_names, lambda c: c==1)
    return to

# Cell
@RegressionSetup
def setups(self, to:Tabular):
    if self.c is not None: return
    self.c = len(to.y_names)
    return to

@RegressionSetup
def encodes(self, to:Tabular): return to

@RegressionSetup
def decodes(self, to:Tabular): return to