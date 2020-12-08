# each cog must contain a setup() method that takes one argument: bot
from os.path import dirname, basename, isfile, join
import glob
modules = glob.glob(join(dirname(__file__), "*.py"))
__all__ = [ basename(f)[:-3] for f in modules if isfile(f) and not f.endswith('__init__.py')]

def setup_all_cogs(bot):
    for cog in __all__:
        exec(cog + '.setup(bot)')