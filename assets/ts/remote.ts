// The remote adapter carries only actual interaction/actions to its own origin.
export {};
type RemoteState = { touched: boolean; remaining: number | null; status: string | null; settled_elsewhere: boolean };
type RemoteWindow = Window & { fenric: { touched: boolean; done: object | null }; fenricSync: (shared: RemoteState) => void };
const remoteWindow = window as unknown as RemoteWindow;
const requestMarker = crypto.randomUUID();
const base = location.pathname.replace(/\/$/, "");
let ended = false;
let reportedTouch = false;
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
    }
    const done = remoteWindow.fenric.done;
    const shared = await exchange(done ?? undefined);
    if (shared.status !== null) ended = true;
    remoteWindow.fenricSync(shared);
  } catch {
    // A lost acknowledgement retries the same action. No value enters diagnostics.
  }
  if (!ended) window.setTimeout(poll, 100);
}
void poll();
