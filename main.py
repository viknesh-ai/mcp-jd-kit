import os
from typing import Any, Dict, List, Union

from fastapi import FastAPI, Request, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from dotenv import load_dotenv

load_dotenv()
AUTH_TOKEN = os.getenv("AUTH_TOKEN")
MY_NUMBER = os.getenv("MY_NUMBER")
PORT = int(os.getenv("PORT", "8086"))

app = FastAPI()


# ---------- JSON-RPC ----------
class JsonRpcRequest(BaseModel):
    jsonrpc: str
    id: Union[str, int, None] = None
    method: str
    params: Dict[str, Any] | None = None


def ok(_id, result):
    return {"jsonrpc": "2.0", "id": _id, "result": result}


def err(_id, code, message):
    return {"jsonrpc": "2.0", "id": _id, "error": {"code": code, "message": message}}


# ---------- utils ----------
def get_token(authorization: str | None, args: Dict[str, Any] | None) -> str | None:
    # header first
    if authorization and authorization.startswith("Bearer "):
        return authorization.split(" ", 1)[1]
    # then payload (MCP tools/call passes params.arguments.token)
    if args and "token" in args:
        return args.get("token")
    return None


# ---------- handlers ----------
def handle_rpc(rpc: JsonRpcRequest, authorization: str | None) -> Dict[str, Any]:
    method = rpc.method
    params = rpc.params or {}

    # 1) MCP handshake
    if method == "initialize":
        return ok(rpc.id, {
            "serverInfo": {"name": "mcp-jd-kit", "version": "0.1.0"},
            "protocolVersion": "2024-06-01",
            "capabilities": {
                "tools": {"listChanged": False}
            }
        })

    # 2) tools/list
    if method == "tools/list":
        return ok(rpc.id, {
            "tools": [
                {
                    "name": "validate",
                    "description": "Return owner phone as a string. Token via Authorization header or arguments.token.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "token": {"type": "string", "description": "Bearer token"}
                        }
                    }
                },
                {
                    "name": "ping",
                    "description": "Liveness check.",
                    "inputSchema": {"type": "object", "properties": {}}
                }
            ]
        })

    # 3) tools/call (Puch calls validate/ping through here)
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {}) if isinstance(params, dict) else {}

        if name == "validate":
            incoming_token = get_token(authorization, arguments)
            if not incoming_token:
                return ok(rpc.id, {"error": {"code": -32001, "message": "Missing token"}})
            if AUTH_TOKEN is None:
                return ok(rpc.id, {"error": {"code": -32003, "message": "Server misconfigured: AUTH_TOKEN missing"}})
            if incoming_token != AUTH_TOKEN:
                return ok(rpc.id, {"error": {"code": -32002, "message": "Invalid token"}})
            if not MY_NUMBER:
                return ok(rpc.id, {"error": {"code": -32000, "message": "Server missing MY_NUMBER"}})
            # MCP tool result content
            return ok(rpc.id, {"content": [{"type": "text", "text": MY_NUMBER}]})

        if name == "ping":
            return ok(rpc.id, {"content": [{"type": "text", "text": "pong"}]})

        return err(rpc.id, -32601, f"Unknown tool: {name}")

    # Optional direct calls (for manual testing)
    if method == "ping":
        return ok(rpc.id, {"pong": True})

    if method == "tools.list":  # backward-compat if a client uses dot form
        return ok(rpc.id, {
            "tools": [
                {
                    "name": "validate",
                    "description": "Return owner phone as string.",
                    "inputSchema": {"type": "object", "properties": {"token": {"type": "string"}}}
                },
                {
                    "name": "ping",
                    "description": "Liveness check.",
                    "inputSchema": {"type": "object", "properties": {}}
                }
            ]
        })

    # Unknown method
    return err(rpc.id, -32601, f"Method not found: {method}")


# ---------- routes ----------
@app.get("/")
def root():
    return {"ok": True, "message": "MCP endpoint at /mcp"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/mcp")
async def mcp(request: Request, authorization: str | None = Header(default=None, convert_underscores=False)):
    # Accept both single and batch JSON-RPC
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(err(None, -32700, "Parse error"), status_code=200)

    try:
        if isinstance(body, list):
            results: List[Dict[str, Any]] = []
            for item in body:
                try:
                    rpc = JsonRpcRequest(**item)
                    results.append(handle_rpc(rpc, authorization))
                except ValidationError:
                    results.append(err(item.get("id", None), -32600, "Invalid Request"))
            return JSONResponse(results, status_code=200)
        else:
            rpc = JsonRpcRequest(**body)
            result = handle_rpc(rpc, authorization)
            return JSONResponse(result, status_code=200)
    except ValidationError:
        return JSONResponse(err(None, -32600, "Invalid Request"), status_code=200)


# ---------- run uvicorn when executed directly (Railway uses: python main.py) ----------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        reload=False
    )
