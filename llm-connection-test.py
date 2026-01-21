import os
from __init__ import *
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder, AIMessagePromptTemplate
from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_community.llms import HuggingFaceHub
# from langchain_huggingface import HuggingFaceEndpoint


# # define the prompt template
# prompt = ChatPromptTemplate.from_messages([
#     ("system", "You are a helpful assistant."),
#     MessagesPlaceholder(variable_name="messages"),
# ])

# # define the output parser
# output_parser = StrOutputParser()


# Azure via LangChain (requires AZURE_OPENAI_* env vars).
client = AzureChatOpenAI(
    temperature=0.7,
    azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2023-12-01-preview"),
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
)

# test the llm connection via the client and role and user message
response = client.invoke(
    [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello, how are you?"},
    ]
)

# print(response.content)

# print only the message of llm from response
# print(response)

# Preferred in LangChain + Pydantic v2
# data = response.model_dump()
# print(data)

# Pretty JSON string
print(response.model_dump_json(indent=2))

# print(response.response_metadata["token_usage"])

# #multiple messages using LangChain batch
# response = client.batch([
#     [
#         {"role": "system", "content": "You are a helpful assistant."},
#         {"role": "user", "content": "Hello, how are you?"},
#     ],
#     [
#         {"role": "system", "content": "You are a helpful assistant."},
#         {"role": "user", "content": "What is the capital of France?"},
#     ],
# ])

# print(response[1].model_dump_json(indent=2))


# # multiple messages using LangChain Messages
# response = client.generate([
#     [
#         SystemMessage(content="You are a helpful assistant."),
#         HumanMessage(content="Hello, how are you?"),
#     ],
#     [
#         SystemMessage(content="You are a helpful assistant."),
#         HumanMessage(content="What is the capital of France?"),
#     ],
# ])

# print(response.generations[0][0].model_dump_json(indent=2))



# # -------------- Langchain with huggingface --------------
# # openai LLM client
# client = AzureChatOpenAI(
#     temperature=0.7,
#     azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
#     azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
#     api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2023-12-01-preview"),
#     api_key=os.getenv("AZURE_OPENAI_API_KEY"),
# )

# repo_id = "google/flan-t5-xxl"
# model_kwargs = {"temperature": 0.7, "max_length": 100}

# from langchain_huggingface import HuggingFaceEndpoint

# llm = HuggingFaceEndpoint(
#     repo_id="google/flan-t5-xxl",
#     task="text2text-generation",
#     model_kwargs={"temperature": 0.7, "max_length": 100},
# )

# print(llm.invoke("Hello, how are you?"))