// The local API. Every request carries this launch's token (the server refuses a POST without it).
const token = document.querySelector('meta[name="talk-autopsy-token"]')?.content ?? "";

export async function api(method, path, body) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json", "X-Talk-Autopsy-Token": token },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    return { ok: false, status: 0, data: { error: "network" } };
  }
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  return { ok: response.ok, status: response.status, data };
}
