"""Local transport of the existing Blender MCP addon's command protocol.

The official MCP tool server uses this same loopback socket. No server installation,
API account, asset download, or automatic alternate renderer is performed here.
"""
import json,socket
from .domain import DomainError

def addon_command(kind,params=None,timeout=180,host='127.0.0.1',port=9876):
    if host not in {'127.0.0.1','localhost','::1'}:raise DomainError('MCP_INVALID','Blender接続はローカルのみです。')
    try:
        with socket.create_connection((host,port),timeout=min(timeout,5)) as conn:
            conn.settimeout(timeout);conn.sendall(json.dumps({'type':kind,'params':params or {}},ensure_ascii=False).encode('utf-8'))
            buffer=bytearray()
            while len(buffer)<8*1024*1024:
                block=conn.recv(65536)
                if not block:raise DomainError('MCP_FAILED','Blenderからの応答が途切れました。')
                buffer.extend(block)
                try:response=json.loads(buffer.decode('utf-8'))
                except (UnicodeDecodeError,json.JSONDecodeError):continue
                if response.get('status')!='success':raise DomainError('MCP_FAILED','Blender MCPの処理が失敗しました。ローカルの作業記録を確認してください。')
                return response.get('result',{})
        raise DomainError('MCP_FAILED','Blender応答が上限を超えました。')
    except (OSError,TimeoutError) as exc:
        raise DomainError('MCP_UNAVAILABLE','Blenderの既存MCP接続を確認してください。別方式へ自動切替しません。') from exc

def execute_file(path,timeout=180):
    # Call sites supply only application-owned files, never arbitrary code from external render requests.
    code='exec(compile(__import__("pathlib").Path('+repr(str(path))+').read_text(encoding="utf-8"),'+repr(str(path))+',"exec"), {"__file__":'+repr(str(path))+',"__name__":"__main__"})'
    return addon_command('execute_code',{'code':code},timeout=timeout)
