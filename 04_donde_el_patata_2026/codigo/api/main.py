from fastapi import FastAPI
from pydantic import BaseModel
from typing import List

from procesador.pedidos import procesar_pedidos

app = FastAPI()


# 📥 Modelo de entrada
class ChatInput(BaseModel):
    mensajes: List[str]


# 📤 Endpoint principal
@app.post("/procesar-chat")
def procesar_chat(data: ChatInput):
    pedidos = procesar_pedidos(data.mensajes)
    return {
        "cantidad_pedidos": len(pedidos),
        "pedidos": pedidos
    }


# 🧪 Endpoint de prueba rápida
@app.get("/")
def home():
    return {"status": "API funcionando 🚀"}