type PeerRequest = {
  socket: { remoteAddress?: string };
};

type GuardResponse = {
  statusCode: number;
  setHeader(name: string, value: string): void;
  end(body?: string): void;
};

type Next = () => void;

export function normalizeLoopbackAddress(address: string | undefined): "127.0.0.1" | null {
  if (address === "127.0.0.1" || address === "::ffff:127.0.0.1") {
    return "127.0.0.1";
  }
  return null;
}

export function isAllowedLoopbackPeer(address: string | undefined): boolean {
  return normalizeLoopbackAddress(address) === "127.0.0.1";
}

export function createLoopbackPeerGuard() {
  return (request: PeerRequest, response: GuardResponse, next: Next): void => {
    if (isAllowedLoopbackPeer(request.socket.remoteAddress)) {
      next();
      return;
    }
    response.statusCode = 403;
    response.setHeader("Content-Type", "text/plain; charset=utf-8");
    response.end("Loopback peer required.");
  };
}
