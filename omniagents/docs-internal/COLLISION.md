│> omni                                                                                                                           │
│Traceback (most recent call last):                                                                                               │
│  File "/home/ericm/.pyenv/versions/3.13.7/bin/omni", line 3, in <module>                                                        │
│    from main import cli_main                                                                                                    │
│  File "/home/ericm/code/omni-code/main.py", line 11, in <module>                                                                │
│    from oagents.coding_agent import create_agent                                                                                │
│  File "/home/ericm/code/omni-code/oagents/coding_agent.py", line 10, in <module>                                                │
│    from tools import (                                                                                                          │
│    ...<14 lines>...                                                                                                             │
│    )                                                                                                                            │
│ImportError: cannot import name 'invoke_slash_command' from 'tools' (/home/ericm/code/demo_agent/tools/__init__.py) 
