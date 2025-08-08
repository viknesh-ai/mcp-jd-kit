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

# -------- JSON-RPC --------
class JsonRpcRequest(BaseModel):
    jsonrpc: str
    id: Union[str, int, None] = None
    method: str
    params: Dict[str, Any] | None = None

def ok(_id, result):
    return {"jsonrpc": "2.0", "id": _id, "result": result}

def err(_id, code, message):
    return {"jsonrpc": "2.0", "id": _id, "error": {"code": code, "message": message}}

# -------- utils --------
def get_token(authorization: str | None, params_or_args: Dict[str, Any] | None) -> str | None:
    # header first
    if authorization and authorization.startswith("Bearer "):
        return authorization.split(" ", 1)[1]
    # then body (MCP tools/call passes in params.arguments)
    if params_or_args:
        # tools/call -> params.arguments.token
        if "token" in params_or_args:
            return params_or_args.get("token")
    return None

# -------- handlers --------
def handle_rpc(rpc: JsonRpcRequest, authorization: str | None) -> Dict[str, Any]:
    method = rpc.method
    params = rpc.params or {}

    # 1) MCP handshake
    if method == "initialize":
        return ok(rpc.id, {
            "serverInfo": { "name": "mcp-jd-kit", "version": "0.1.0" },
            "protocolVersion": "2024-06-01",
            "capabilities": {
                "tools": { "listChanged": False }
            }
        })

    # 2) tools/list (return tool metadata with inputSchema)
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
                    "inputSchema": { "type": "object", "properties": {} }
                }
            ]
        })

    # 3) tools/call (Puch will call validate via this)
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {}) if isinstance(params, dict) else {}
        # --- validate ---
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
            # MCP tool result should live under "result"
            return ok(rpc.id, {"content": [{"type": "text", "text": MY_NUMBER}]})

        # --- ping ---
        if name == "ping":
            return ok(rpc.id, {"content": [{"type": "text", "text": "pong"}]})

        return err(rpc.id, -32601, f"Unknown tool: {name}")

    # (Optional dev helpers)
    if method == "ping":
        return ok(rpc.id, {"pong": True})

    if method == "tools.list":  # backward-compat for earlier attempt
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

# -------- routes --------
@app.get("/")
def root():
    return {"ok": True, "message": "MCP endpoint at /mcp"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/mcp")
async def mcp(request: Request, authorization: str | None = Header(default=None, convert_underscores=False)):
    try:
        raw = await request.body()
        body_text = raw.decode("utf-8", errors="replace")
        print("\n====== MCP REQUEST ======")
        print(body_text)
    except Exception as e:
        print(f"\n[log] failed reading body: {e}")
        return JSONResponse(err(None, -32700, "Parse error"), status_code=200)

    # Try to parse JSON
    try:
        body = await request.json()
    except Exception as e:
        print(f"[log] JSON parse error: {e}")
        resp = err(None, -32700, "Parse error")
        print("====== MCP RESPONSE ======")
        print(resp)
        return JSONResponse(resp, status_code=200)

    # Handle single or batch
    try:
        if isinstance(body, list):
            results: List[Dict[str, Any]] = []
            for item in body:
                try:
                    rpc = JsonRpcRequest(**item)
                    results.append(handle_rpc(rpc, authorization))
                except ValidationError as ve:
                    print(f"[log] Validation error in batch item: {ve}")
                    results.append(err(item.get("id", None), -32600, "Invalid Request"))
            print("====== MCP RESPONSE ======")
            print(results)
            return JSONResponse(results, status_code=200)
        else:
            rpc = JsonRpcRequest(**body)
            result = handle_rpc(rpc, authorization)
            print("====== MCP RESPONSE ======")
            print(result)
            return JSONResponse(result, status_code=200)
    except ValidationError as ve:
        print(f"[log] Validation error: {ve}")
        resp = err(None, -32600, "Invalid Request")
        print("====== MCP RESPONSE ======")
        print(resp)
        return JSONResponse(resp, status_code=200)
