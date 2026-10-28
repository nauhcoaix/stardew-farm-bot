"""命令行与依赖组装。

这是唯一允许同时导入通信、模型、经验与能力的地方：它负责把公开函数组合成一次运行。
"""

from .config import AgentConfig, config_from_mapping, load_config
from .main import main

__all__ = ["AgentConfig", "config_from_mapping", "load_config", "main"]
