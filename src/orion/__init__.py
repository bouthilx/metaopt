from orion.core.io.config import Configuration
from orion.core.io import resolve_config

# Default values
# env vars
# Config files
# Command line

# DB only concerns experiment and thus should not affect configuration.

config = Configuration()
resolve_config.define_config(config)
resolve_config.parse_config_files(config)

# Define config
# database
# resources
# command specific
#     branching

# Set config
# Load global config
# command specific
#     parse config file
#     parse args
