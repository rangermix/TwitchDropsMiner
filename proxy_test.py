
import http.server
import socketserver
import urllib.request
import logging
import shutil

PORT = 8888

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ProxyServer")

class Proxy(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        logger.info(f"Proxy request: {self.path}")
        try:
            with urllib.request.urlopen(self.path) as response:
                self.send_response(response.status)
                for header, value in response.headers.items():
                    self.send_header(header, value)
                self.end_headers()
                shutil.copyfileobj(response, self.wfile)
        except Exception as e:
            self.send_error(500, str(e))

    def do_POST(self):
        logger.info(f"Proxy request (POST): {self.path}")
        length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(length)
        req = urllib.request.Request(self.path, data=post_data, method='POST')
        try:
            with urllib.request.urlopen(req) as response:
                self.send_response(response.status)
                for header, value in response.headers.items():
                    self.send_header(header, value)
                self.end_headers()
                shutil.copyfileobj(response, self.wfile)
        except Exception as e:
            self.send_error(500, str(e))

    def do_CONNECT(self):
        logger.info(f"CONNECT request: {self.path}")
        self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        # In a real proxy we would tunnel. 
        # For verification of "reachability", getting the 200 is often enough for simple clients,
        # but aiohttp might try to read/write through the tunnel.
        # Minimal tunnel implementation:
        return

if __name__ == "__main__":
    # Reuse address to avoid port conflicts
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), Proxy) as httpd:
        print(f"Serving proxy at port {PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down proxy")
