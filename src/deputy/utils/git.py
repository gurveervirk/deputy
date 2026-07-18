import subprocess
from deputy.logger import get_logger

logger = get_logger("utils.git")

def get_current_branch():
    """Get the current git branch name."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True
        )
        branch = result.stdout.strip()
        logger.debug("current branch: %s", branch)
        return branch
    except subprocess.CalledProcessError as e:
        logger.error("Error getting current branch: %s", e.stderr.strip())
        return None