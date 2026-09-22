import streamlit as st

from graph import graph
import data_tools as dt

st.set_page_config(page_title="Real Estate Asset Manager", page_icon="🏢")
st.title("🏢 Real Estate Asset Manager")
st.caption(
    "Ask about P&L, compare properties or periods, top tenants, or unusual entries. "
    f"Dataset: {len(dt.get_properties())} properties, {len(dt.get_tenants())} tenants, "
    f"years {', '.join(dt.get_years())}."
)

with st.expander("Example questions"):
    st.markdown(
        "- What is the total P&L for all my properties this year?\n"
        "- How does this quarter compare to the same period last year?\n"
        "- Who are my top tenants, and is anything unusual in the numbers?\n"
        "- What is the price of my asset at 123 Main St compared to the one at 456 Oak Ave?\n"
        "- Compare Building 17 to Building 140.\n"
        "- Compare Building 17 to Building 500 *(triggers a clarification)*"
    )

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("Ask about your properties..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Working..."):
            try:
                final_state = graph.invoke({"query": prompt})
                response = final_state.get("response", "I couldn't produce an answer for that.")
            except Exception as e:
                response = f"Something went wrong processing that request: {e}"
        st.markdown(response)

        with st.expander("Show agent internals"):
            st.json(
                {
                    "intents": final_state.get("intents"),
                    "entities": final_state.get("entities"),
                    "validation_errors": final_state.get("validation_errors"),
                    "results": final_state.get("results"),
                }
            )

    st.session_state.messages.append({"role": "assistant", "content": response})
