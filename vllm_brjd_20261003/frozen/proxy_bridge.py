"""Task-owned WSL-to-Windows loopback proxy bridge, bound only to the WSL adapter."""
import ipaddress, select, socket, socketserver, subprocess, threading

def gateway():
    p=subprocess.run(['wsl','-d','Ubuntu-22.04','--','ip','route','show','default'],
                     capture_output=True,timeout=30)
    output=p.stdout.decode('utf-8','replace').replace('\x00','')
    value=output.split()[2];ipaddress.ip_address(value);return value

class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        if not ipaddress.ip_address(self.client_address[0]) in self.server.allowed: return
        try:
            with socket.create_connection(('127.0.0.1',7890),timeout=10) as upstream:
                self.request.settimeout(90);upstream.settimeout(90)
                sockets=[self.request,upstream]
                while True:
                    ready,_,_=select.select(sockets,[],[],90)
                    if not ready:return
                    for current in ready:
                        data=current.recv(65536)
                        if not data:return
                        (upstream if current is self.request else self.request).sendall(data)
        except (OSError,TimeoutError):return

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=True

def start_bridge():
    host=gateway();server=Server((host,27890),Handler)
    server.allowed=ipaddress.ip_network(host+'/20',strict=False)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    return server,'http://'+host+':27890'

if __name__=='__main__':
    server,url=start_bridge();print(url,flush=True)
    try:threading.Event().wait()
    except KeyboardInterrupt:pass
    finally:server.shutdown();server.server_close()
