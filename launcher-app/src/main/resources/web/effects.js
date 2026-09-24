const FX = (() => {
  function setEnabled(on) {
    document.documentElement.dataset.motion = on ? "full" : "reduced";
  }

  return { setEnabled };
})();
