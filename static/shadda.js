/* The pages besides index.html: a Hebrew letter carrying the Arabic doubling mark (U+0651) shows as an empty box,
   since no Hebrew font places that mark. Each such letter becomes <span class="sh">, with the mark drawn above it
   (as index.html does). Text added later is fixed as it arrives. */
(() => {
  const SHADDA = /([א-ת]['׳]?[֑-ׇ]*)ّ([֑-ׇ]*)/g;
  const HAS = /[א-ת]['׳]?[֑-ׇ]*ّ/;
  const css = document.createElement("style");
  css.textContent = `.sh{position:relative}.sh::before{content:"";position:absolute;top:-.04em;left:50%;transform:translateX(-50%);
    width:.44em;height:.26em;background:currentColor;-webkit-mask:var(--shadda-mark) center/contain no-repeat;mask:var(--shadda-mark) center/contain no-repeat}
    :root{--shadda-mark:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 6'%3E%3Cpath d='M1 1v2a2 2 0 0 0 4 0v-1M5 3a2 2 0 0 0 4 0v-2' fill='none' stroke='%23000' stroke-width='1.3' stroke-linecap='round'/%3E%3C/svg%3E")}`;
  document.head.append(css);
  const fix = root => {
    if (root.nodeType === 3) return root.parentNode && fix(root.parentNode);
    if (root.nodeType !== 1 && root.nodeType !== 11) return;
    const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: n => HAS.test(n.nodeValue) && !n.parentElement?.closest("script,style,textarea,title")
        ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT });
    const found = [];
    while (walk.nextNode()) found.push(walk.currentNode);
    for (const n of found) {
      if (n.parentElement?.closest("option,select")) { n.nodeValue = n.nodeValue.replace(SHADDA, "$1$2"); continue; }
      const parts = [];
      let at = 0;
      for (const m of n.nodeValue.matchAll(SHADDA)) {
        const sp = document.createElement("span");
        sp.className = "sh";
        sp.textContent = m[1] + m[2];
        parts.push(n.nodeValue.slice(at, m.index), sp);
        at = m.index + m[0].length;
      }
      parts.push(n.nodeValue.slice(at));
      n.replaceWith(...parts);
    }
  };
  const start = () => {
    fix(document.body);
    new MutationObserver(list => {
      for (const m of list) m.type === "characterData" ? fix(m.target) : m.addedNodes.forEach(fix);
    }).observe(document.body, { childList: true, subtree: true, characterData: true });
  };
  document.body ? start() : addEventListener("DOMContentLoaded", start);
})();
