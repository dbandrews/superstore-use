# Converting main.py to LangGraph Conversational App

## Architecture Overview

```
User Chat → Main Agent → Tools (add_to_cart, checkout) → Browser Agents
                ↑                    ↓
                └── Human-in-the-loop (checkout approval)
```

## Key Components

### 1. Use `create_agent` (High-level API)

```python
from langchain.agents import create_agent
from langchain.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

@tool
def add_item_to_cart(item: str) -> str:
    """Add an item to the Real Canadian Superstore cart."""
    # Your browser_use logic here
    return f"Added {item} to cart"

@tool
def checkout() -> str:
    """Proceed to checkout."""
    # Your checkout logic here
    return "Checkout complete"

agent = create_agent(
    model="gpt-4o",
    tools=[add_item_to_cart, checkout],
    checkpointer=InMemorySaver(),  # Required for persistence
)
```

### 2. Human-in-the-Loop for Checkout Approval

```python
from langchain.agents.middleware import HumanInTheLoopMiddleware

agent = create_agent(
    model="gpt-4o",
    tools=[add_item_to_cart, checkout],
    middleware=[
        HumanInTheLoopMiddleware(
            interrupt_on={
                "checkout": True,  # Requires approval
                "add_item_to_cart": False,  # Auto-approve
            }
        ),
    ],
    checkpointer=InMemorySaver(),
)
```

### 3. Running the Agent (Chat Loop)

```python
from langgraph.types import Command

config = {"configurable": {"thread_id": "shopping-session-1"}}

# User sends message
result = agent.invoke(
    {"messages": [{"role": "user", "content": "Add milk and eggs to my cart"}]},
    config=config
)

# If interrupted for checkout approval
if "__interrupt__" in result:
    # Show user what's being requested
    print(result["__interrupt__"])

    # Resume with approval
    result = agent.invoke(
        Command(resume={"decisions": [{"type": "approve"}]}),
        config=config
    )
```

### 4. Custom StateGraph (Lower-level Control)

For more control, use `StateGraph` directly:

```python
from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.checkpoint.memory import MemorySaver

def chat_node(state: MessagesState):
    # Call LLM with tools bound
    response = llm.bind_tools([add_item_to_cart, checkout]).invoke(state["messages"])
    return {"messages": [response]}

def tools_node(state: MessagesState):
    # Execute tool calls
    ...

workflow = StateGraph(MessagesState)
workflow.add_node("chat", chat_node)
workflow.add_node("tools", tools_node)
workflow.add_edge(START, "chat")
# Add conditional edges for tool calling loop

graph = workflow.compile(checkpointer=MemorySaver())
```

### 5. Agent Chat UI (Optional)

LangChain provides a pre-built chat interface:
- **Agent Chat UI** - Next.js app at `http://localhost:2024`
- Supports real-time streaming, tool visualization, time-travel debugging
- Run with `langgraph dev` CLI

## Mapping Current Code to LangGraph

| Current (`main.py`) | LangGraph Equivalent |
|---------------------|---------------------|
| `collect_items_from_user()` | Chat messages to agent |
| `add_single_item_process()` | `@tool add_item_to_cart()` |
| `confirm_checkout()` | `HumanInTheLoopMiddleware` interrupt |
| `checkout()` | `@tool checkout()` with approval |
| Multiprocessing | Tools run sequentially or use async |

## Sources

- [LangGraph Overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [Agents](https://docs.langchain.com/oss/python/langchain/agents)
- [Human-in-the-loop](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)
- [Agent Chat UI](https://docs.langchain.com/oss/python/langgraph/ui)
- [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
- [Multi-agent](https://docs.langchain.com/oss/python/langchain/multi-agent)
- [Workflows and Agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)
- [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
