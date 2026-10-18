from app.tools.analyze_paper import TOOL as ANALYZE_PAPER
from app.tools.analyze_paper import analyze_paper as run_analyze_paper
from app.tools.index_paper import TOOL as INDEX_PAPER
from app.tools.index_paper import index_paper as run_index_paper
from app.tools.search_papers import TOOL as SEARCH_PAPERS
from app.tools.search_papers import search_papers as run_search_papers
from app.tools.add_papers_to_workspace import TOOL as ADD_PAPERS_TO_WORKSPACE
from app.tools.add_papers_to_workspace import add_papers_to_workspace as run_add_papers_to_workspace

from app.tools.parse_paper import TOOL as PARSE_PAPER
from app.tools.parse_paper import parse_paper as run_parse_paper

from app.tools.acquire_paper_fulltext import TOOL as ACQUIRE_PAPER_FULLTEXT
from app.tools.acquire_paper_fulltext import acquire_paper_fulltext as run_acquire_paper_fulltext
from app.tools.research import CHAT_TOOLS, FUNCTION_TOOLS

# Only these fourteen business tools are advertised to the chat model.
TOOLS = CHAT_TOOLS
BACKGROUND_TOOLS = (ACQUIRE_PAPER_FULLTEXT, PARSE_PAPER, INDEX_PAPER)
