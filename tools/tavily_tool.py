from tavily import TavilyClient
import os
from dotenv import load_dotenv
load_dotenv()


client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

def tavily_search(query: str) -> str:
    """Performs a web search using the Tavily API and returns formatted search results.

    Args:
        query (str): The search query string.

    Returns:
        str: Formatted search results containing titles, URLs, and truncated snippets,
             or an error message string if the search fails.
    """
    try:
        response = client.search(query=query, max_results=5)
        result = []
        for i, r in enumerate(response["results"], 1):
            title = r.get('title', 'Unknown')
            url = r.get('url', '')
            snippet = r.get('content', '')
            # Keep only the first 300 characters to avoid wall-of-text
            if len(snippet) > 300:
                snippet = snippet[:300].rsplit(" ", 1)[0] + "..."
            result.append(f"[{i}] **{title}**\n {url}\n {snippet}")
        return "\n\n".join(result)
                
    except Exception as e:
        return f"Error in tavily search: {e}"