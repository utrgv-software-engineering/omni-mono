// The workplace's runtime for canvas boards (<Name>.dc.html), as the design
// canvas writes them:
//
//   <script src="./support.js"></script>          in the head
//   <x-dc>
//     <helmet> ...head content: fonts, styles... </helmet>
//     ...the board: {{values}} in text and attributes,
//        <sc-if value="{{flag}}">...</sc-if>,
//        <sc-for list="{{items}}" as="item">...{{item.label}}...</sc-for>,
//        onClick="{{handler}}"
//   </x-dc>
//   <script type="text/x-dc" data-props='{"$preview": {...}}'>
//     class Component extends DCLogic {
//       constructor(props) { super(props); this.state = {...}; }
//       renderVals() { return {flag: ..., items: [...], handler: () => this.setState({...})}; }
//     }
//   </script>
//
// The board's values come from renderVals(); setState renders it again. A
// board whose script fails shows its markup as it is, with a note.
(function () {
  "use strict";

  class DCLogic {
    constructor(props) {
      this.props = props || {};
      this.state = {};
      this.__render = null;
    }
    setState(patch) {
      const next = typeof patch === "function" ? patch(this.state, this.props) : patch;
      this.state = Object.assign({}, this.state, next || {});
      if (this.__render) this.__render();
    }
    renderVals() {
      return {};
    }
  }

  const WHOLE = /^\s*\{\{([^}]*)\}\}\s*$/;
  const PART = /\{\{([^}]*)\}\}/g;
  const BOOLEAN = new Set(["disabled", "checked", "selected", "hidden", "readonly", "required", "open", "multiple"]);

  /** A {{binding}}'s value: a literal, or a dotted path in scope (``!`` negates). */
  function evaluate(expr, scope) {
    let text = String(expr).trim();
    let negate = false;
    while (text.startsWith("!")) {
      negate = !negate;
      text = text.slice(1).trim();
    }
    let value;
    if (text === "true") value = true;
    else if (text === "false") value = false;
    else if (text === "null" || text === "") value = null;
    else if (text === "undefined") value = undefined;
    else if (/^-?\d+(\.\d+)?$/.test(text)) value = Number(text);
    else if (/^(['"]).*\1$/.test(text)) value = text.slice(1, -1);
    else {
      value = scope;
      for (const key of text.split(".")) {
        if (value === null || value === undefined) break;
        value = value[key];
      }
    }
    return negate ? !value : value;
  }

  function show(value) {
    return value === null || value === undefined || value === false ? "" : String(value);
  }

  function interpolate(text, scope) {
    return text.replace(PART, (_, expr) => show(evaluate(expr, scope)));
  }

  /** The nodes ``template``'s children make in ``scope``. */
  function renderChildren(template, scope, doc) {
    const out = [];
    for (const node of template.childNodes) out.push(...renderNode(node, scope, doc));
    return out;
  }

  function renderNode(node, scope, doc) {
    if (node.nodeType === 3) return [doc.createTextNode(interpolate(node.nodeValue, scope))];
    if (node.nodeType !== 1) return [];
    const tag = node.localName;
    if (tag === "sc-if") {
      const value = evaluate(bindingOf(node.getAttribute("value")), scope);
      return value ? renderChildren(node, scope, doc) : [];
    }
    if (tag === "sc-for") {
      const list = evaluate(bindingOf(node.getAttribute("list")), scope);
      const as = node.getAttribute("as") || "item";
      const out = [];
      if (Array.isArray(list))
        list.forEach((item, index) => {
          const inner = Object.assign(Object.create(scope), { [as]: item, [`${as}Index`]: index });
          out.push(...renderChildren(node, inner, doc));
        });
      return out;
    }
    if (tag === "script" || tag === "helmet") return [];
    const el = node.namespaceURI && node.namespaceURI !== "http://www.w3.org/1999/xhtml"
      ? doc.createElementNS(node.namespaceURI, node.tagName)
      : doc.createElement(tag);
    for (const attr of node.attributes) {
      const whole = WHOLE.exec(attr.value);
      const name = attr.name;
      if (name.startsWith("on")) {
        const handler = whole ? evaluate(whole[1], scope) : null;
        if (typeof handler === "function") {
          const kind = name.slice(2).toLowerCase();
          el.addEventListener(kind, (event) => handler(event));
        }
        continue;
      }
      if (name.startsWith("hint-")) continue;
      if (whole) {
        const value = evaluate(whole[1], scope);
        if (value === false || value === null || value === undefined) {
          if (!BOOLEAN.has(name) && name.startsWith("aria-")) el.setAttribute(name, "false");
          continue;
        }
        el.setAttribute(name, BOOLEAN.has(name) && value === true ? "" : String(value));
        if (name === "value" && "value" in el) el.value = String(value);
      } else {
        el.setAttribute(name, interpolate(attr.value, scope));
      }
    }
    const content = tag === "template" ? node.content : node;
    for (const child of renderChildren(content, scope, doc)) el.appendChild(child);
    return [el];
  }

  function bindingOf(value) {
    const whole = WHOLE.exec(value || "");
    return whole ? whole[1] : value || "";
  }

  /** Run the board in ``doc``: move its helmet into the head, run its component, render. */
  function mount(doc) {
    doc = doc || document;
    const root = doc.querySelector("x-dc");
    if (!root) return null;
    const helmet = root.querySelector("helmet");
    if (helmet) {
      for (const child of [...helmet.childNodes]) doc.head.appendChild(child);
      helmet.remove();
    }
    const template = doc.createElement("template");
    for (const child of [...root.childNodes]) template.content.appendChild(child);
    root.style.display = "contents";
    const script = doc.querySelector('script[type="text/x-dc"]');
    let props = {};
    try {
      props = JSON.parse((script && script.getAttribute("data-props")) || "{}");
    } catch (e) {
      props = {};
    }
    delete props.$preview;
    let component = null;
    try {
      if (script) {
        const make = new Function(
          "DCLogic",
          `${script.textContent}\n;return typeof Component !== "undefined" ? Component : null;`,
        );
        const Component = make(DCLogic);
        if (Component) component = new Component(props);
      }
    } catch (error) {
      return fail(doc, root, template, error);
    }
    const render = () => {
      let vals = {};
      try {
        vals = component ? component.renderVals() || {} : {};
      } catch (error) {
        return fail(doc, root, template, error);
      }
      const scope = Object.assign(Object.create(null), props, vals);
      root.replaceChildren(...renderChildren(template.content, scope, doc));
    };
    if (component) component.__render = render;
    render();
    doc.documentElement.setAttribute("data-dc-ready", "");
    return { component, render, root };
  }

  function fail(doc, root, template, error) {
    root.replaceChildren(template.content.cloneNode(true));
    const note = doc.createElement("div");
    note.textContent = `This board's script did not run: ${error && error.message ? error.message : error}`;
    note.setAttribute(
      "style",
      "position:fixed;bottom:8px;left:8px;z-index:99999;font:12px system-ui;background:#FEF3C7;color:#78350F;" +
        "border:1px solid #F59E0B;border-radius:6px;padding:4px 8px",
    );
    doc.body.appendChild(note);
    doc.documentElement.setAttribute("data-dc-ready", "failed");
    return null;
  }

  const api = { DCLogic, mount, evaluate };
  if (typeof window !== "undefined") {
    window.DCLogic = DCLogic;
    window.__dc = api;
    // The board's raw template stays hidden until it is rendered.
    const hide = document.createElement("style");
    hide.textContent = "x-dc{visibility:hidden}html[data-dc-ready] x-dc{visibility:visible}";
    (document.head || document.documentElement).appendChild(hide);
    if (!window.__dcNoAutoMount) {
      if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => mount(document));
      else mount(document);
    }
  }
  if (typeof module !== "undefined") module.exports = api;
})();
