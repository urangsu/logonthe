(() => {
  // Install before site handlers. This is editor scoped, never a global shortcut.
  const selector = '#naverComment__write_textarea,div.u_cbox_text[contenteditable="true"],div.u_cbox_write_box div[contenteditable="true"],textarea.u_cbox_text';
  function guard(event) {
    if (!(event.target instanceof Element) || !event.target.matches(selector) || event.key !== "Enter") return;
    event.stopImmediatePropagation();
    // Keep the IME's default composition commit, but never propagate it to submit handlers.
    if (!event.isComposing && event.keyCode !== 229) event.preventDefault();
  }
  for (const name of ["keydown", "keypress", "keyup"]) window.addEventListener(name, guard, true);
})();
