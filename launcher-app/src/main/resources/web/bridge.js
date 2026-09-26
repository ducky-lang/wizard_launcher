const WL = (() => {
  const handlers = {};
  function call(cmd, args = {}) {
    return new Promise((resolve, reject) => {
      if (typeof window.cefQuery !== "function") {
        reject(new Error("bridge unavailable"));
        return;
      }
      window.cefQuery({
        request: JSON.stringify({ cmd, args }),
        persistent: false,
        onSuccess: (response) => {
          try { resolve(response ? JSON.parse(response) : null); } catch (e) { resolve(response); }
        },
        onFailure: (code, message) => reject(new Error(message || ("error " + code))),
      });
    });
  }
  function on(event, fn) {
    (handlers[event] = handlers[event] || []).push(fn);
  }
  function emit(event, data) {
    (handlers[event] || []).forEach((fn) => { try { fn(data); } catch (e) { console.error(e); } });
  }
  window.__wl_emit = emit;
  return { call, on, emit };
})();
