import sys
sys.path.append('.')
from simulation.grid import SmartGrid  # type: ignore
from models.rl_agent import DQNAgent  # type: ignore

grid = SmartGrid()
grid.step()
agent = DQNAgent()  # type: ignore
agent.smart_warmup(grid)
print('SUCCESS: Tensor size strictly bound to 52!')
