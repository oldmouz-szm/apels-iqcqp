"""Empty full-archive bootstrap, independent of task-construction RNG."""
class PersistentBootstrap:
    def __init__(self, dimensions, strategy='persistent_unit_v2'):
        self.dimensions=dimensions
        self.strategy=strategy
        self.completions=0
        self.submissions=0
    def task(self, worker):
        n=self.dimensions;self.submissions+=1
        return dict(kind='feasibility',weights=[float(j==worker%n) for j in range(n)],
                    eps=[None]*n,seed=None,z=[0]*n,scale=[1]*n,scale_version=0,gap=None,
                    logical_task_id=f'bootstrap:{worker}',
                    bootstrap_reason='empty_archive',bootstrap_strategy=self.strategy,
                    decision=dict(eligible=['feasibility'],forced_reason='empty_archive'))
