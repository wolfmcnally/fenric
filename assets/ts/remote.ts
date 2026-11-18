// The remote adapter carries only actual interaction/actions to its own origin.
export {};
type RemoteState = { touched: boolean; remaining: number | null; status: string | null; settled_elsewhere: boolean };
type RemoteWindow = Window & { fenric: { touched: boolean; done: object | null }; fenricSync: (shared: RemoteState) => void };
const remoteWindow = window as unknown as RemoteWindow;
// randomUUID requires a secure context; trusted-network HTTP still provides
// getRandomValues. Keep the same canonical version-4 marker on both transports.
const markerBytes = crypto.getRandomValues(new Uint8Array(16));
markerBytes[6] = (markerBytes[6] & 0x0f) | 0x40;
markerBytes[8] = (markerBytes[8] & 0x3f) | 0x80;
const requestMarker = Array.from(markerBytes, (byte, index) =>
  ([4, 6, 8, 10].includes(index) ? "-" : "") + byte.toString(16).padStart(2, "0")
).join("");
const base = location.pathname.replace(/\/$/, "");
let ended = false;
let reportedTouch = false;
let failures = 0;
async function exchange(action?: object): Promise<RemoteState> {
  const response = await fetch(base + (action ? "/action" : "/state"), {
    method: action ? "POST" : "GET", cache: "no-store", credentials: "omit",
    headers: { "Fenric-Request": requestMarker, ...(action ? { "Content-Type": "application/json" } : {}) },
    body: action ? JSON.stringify(action) : undefined,
    signal: AbortSignal.timeout(3000),
  });
  if (!response.ok) throw new Error("refused");
  return await response.json() as RemoteState;
}
async function poll(): Promise<void> {
  if (ended) return;
  try {
    if (remoteWindow.fenric.touched && !reportedTouch) {
      await exchange({ kind: "touch" });
      reportedTouch = true;
      failures = 0;
    }
    const done = remoteWindow.fenric.done;
    const shared = await exchange(done ?? undefined);
    failures = 0;
    if (shared.status !== null) ended = true;
    remoteWindow.fenricSync(shared);
  } catch {
    // Retry a transient loss, but never hammer a removed route indefinitely.
    // A missing acknowledgement cannot prove whether an answer was accepted.
    if (++failures >= 3) {
      ended = true;
      remoteWindow.fenricSync({
        touched: remoteWindow.fenric.touched, remaining: null,
        status: "Connection unavailable. This view may have expired. Any response is unconfirmed.",
        settled_elsewhere: false,
      });
    }
  }
  if (!ended) window.setTimeout(poll, failures ? 500 * failures : 100);
}
void poll();
