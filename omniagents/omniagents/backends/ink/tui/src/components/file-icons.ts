// File-type icons, generated from mini.icons — the icon provider
// LazyVim's neo-tree renders with. Glyph AND colour class both come from
// its tables, so a `.py` here is the same icon in the same colour as a
// `.py` there.
//
// Directory glyphs are neo-tree's own `folder_closed`/`folder_open`
// instead: neo-tree draws directories itself and only delegates FILES to
// the icon provider.
//
// These are Nerd Font glyphs. A terminal without one renders tofu, so
// the column is opt-out with `OMNI_INK_ICONS=0`.
//
// mini.icons' colours are highlight links (MiniIconsRed ->
// DiagnosticError, and so on) resolved against the active colour scheme.
// Ours is Tokyo Night and so is theirs, so the classes land on the
// palette this TUI already has.

import { roleMap } from "../theme.js";

export type IconClass =
  | "Red"
  | "Yellow"
  | "Orange"
  | "Green"
  | "Blue"
  | "Azure"
  | "Cyan"
  | "Purple"
  | "Grey";

export const ICON_COLORS: Record<IconClass, string> = roleMap({
  Red: "stateFailed",
  // mini.icons links Orange and Yellow to the same DiagnosticWarn.
  Yellow: "stateBlocked",
  Orange: "stateBlocked",
  Green: "stateDone",
  Blue: "accentAgent",
  // No separate azure; DiagnosticInfo is the agent blue.
  Azure: "accentAgent",
  Cyan: "stateActive",
  Purple: "accentUser",
  Grey: "textChrome",
});

type Entry = readonly [glyph: string, cls: IconClass];

/** Exact basenames win over extensions, as in mini.icons. */
const BY_NAME: Record<string, Entry> = {
  ".DS_Store": ["󰒓", "Red"],
  ".bash_profile": ["󰒓", "Green"],
  ".bashrc": ["󰒓", "Green"],
  ".containerignore": ["󰒓", "Grey"],
  ".dockerignore": ["󰡨", "Orange"],
  ".fdignore": ["󰒓", "Yellow"],
  ".git": ["󰊢", "Orange"],
  ".gitkeep": ["󰊢", "Red"],
  ".gitlab-ci.yml": ["󰮠", "Orange"],
  ".gitolite.rc": ["", "Azure"],
  ".ignore": ["󰒓", "Grey"],
  ".indent.pro": ["󰉶", "Green"],
  ".mailmap": ["󰊢", "Cyan"],
  ".npmignore": ["󰒓", "Grey"],
  ".nvmrc": ["󰒓", "Green"],
  ".oelint.cfg": ["󰯂", "Azure"],
  ".prettierignore": ["", "Orange"],
  ".rgignore": ["󰒓", "Yellow"],
  ".vscodeignore": ["", "Azure"],
  ".wakatime.cfg": ["󰯂", "Azure"],
  ".xinitrc": ["󰒓", "Blue"],
  ".zshrc": ["󰒓", "Green"],
  CHANGELOG: ["󰉻", "Blue"],
  "CHANGELOG.md": ["󰉻", "Blue"],
  "CMakeCache.txt": ["󱁤", "Red"],
  "CMakeLists.txt": ["󱁤", "Orange"],
  CODEOWNERS: ["󰜻", "Purple"],
  CODE_OF_CONDUCT: ["󱃱", "Red"],
  "CODE_OF_CONDUCT.md": ["󱃱", "Red"],
  CONTRIBUTING: ["󰺾", "Azure"],
  "CONTRIBUTING.md": ["󰺾", "Azure"],
  Containerfile: ["󰡨", "Blue"],
  Dockerfile: ["󰡨", "Blue"],
  "FUNDING.yml": ["󰇁", "Green"],
  GNUmakefile: ["󱁤", "Grey"],
  "GNUmakefile.am": ["󱁤", "Purple"],
  Justfile: ["󰖷", "Orange"],
  LICENSE: ["", "Cyan"],
  "LICENSE.md": ["", "Cyan"],
  "LICENSE.txt": ["", "Cyan"],
  Makefile: ["󱁤", "Grey"],
  "Makefile.am": ["󱁤", "Purple"],
  NEWS: ["󰎕", "Blue"],
  "NEWS.md": ["󰎕", "Blue"],
  PKGBUILD: ["󱁤", "Purple"],
  README: ["", "Yellow"],
  "README.md": ["", "Yellow"],
  "README.txt": ["", "Yellow"],
  TODO: ["󰝖", "Purple"],
  "TODO.md": ["󰝖", "Purple"],
  "auto.master": ["󰒓", "Grey"],
  "build.xml": ["󰫮", "Red"],
  "cm3.cfg": ["󰫺", "Green"],
  "config.ld": ["󰢱", "Azure"],
  "constraints.txt": ["󱘎", "Purple"],
  "example.gitolite.rc": ["", "Azure"],
  "gitolite.rc": ["", "Azure"],
  "go.sum": ["󰟓", "Cyan"],
  "go.work.sum": ["󰟓", "Cyan"],
  "indent.pro": ["󰉶", "Green"],
  "init.lua": ["", "Green"],
  "ipf.rules": ["󰒓", "Azure"],
  justfile: ["󰖷", "Orange"],
  "lltxxxxx.txt": ["󰫴", "Red"],
  "lynx.cfg": ["󰒓", "Red"],
  "main.cf.proto": ["󰒓", "Orange"],
  makefile: ["󱁤", "Grey"],
  "makefile.am": ["󱁤", "Purple"],
  "maxima-init.mac": ["󰫺", "Grey"],
  "meson_options.txt": ["󰫺", "Blue"],
  "pudb.cfg": ["󰯂", "Azure"],
  "requirements.txt": ["󱘎", "Purple"],
  "robots.txt": ["󰚩", "Grey"],
  "setup.cfg": ["󰯂", "Azure"],
  "tclsh.rc": ["󰛓", "Red"],
};

const BY_EXTENSION: Record<string, Entry> = {
  "3gp": ["󰈫", "Yellow"],
  "7z": ["󰗄", "Blue"],
  aac: ["󰈣", "Yellow"],
  aif: ["󰈣", "Cyan"],
  avi: ["󰈫", "Grey"],
  bash: ["", "Green"],
  bmp: ["󰈟", "Green"],
  bz: ["󰗄", "Orange"],
  bz2: ["󰗄", "Orange"],
  bz3: ["󰗄", "Orange"],
  c: ["󰙱", "Blue"],
  cast: ["󰈫", "Red"],
  "code-snippets": ["󰘦", "Yellow"],
  conf: ["󰒓", "Grey"],
  cpp: ["󰙲", "Azure"],
  css: ["󰌜", "Azure"],
  csv: ["", "Green"],
  diff: ["󰦓", "Red"],
  doc: ["󱎒", "Azure"],
  docm: ["󱎒", "Azure"],
  docx: ["󱎒", "Azure"],
  dot: ["󱎒", "Azure"],
  dotx: ["󱎒", "Azure"],
  eps: ["", "Red"],
  exe: ["󰖳", "Red"],
  exs: ["", "Purple"],
  flac: ["󰈣", "Orange"],
  gif: ["󰵸", "Azure"],
  go: ["󰟓", "Azure"],
  gz: ["󰗄", "Grey"],
  h: ["󰫵", "Purple"],
  html: ["󰌝", "Orange"],
  ini: ["󰯂", "Azure"],
  ipynb: ["󰠮", "Orange"],
  java: ["󰬷", "Orange"],
  jpeg: ["󰈥", "Orange"],
  jpg: ["󰈥", "Orange"],
  js: ["󰌞", "Yellow"],
  json: ["󰘦", "Yellow"],
  jsx: ["󰌞", "Yellow"],
  lua: ["󰢱", "Azure"],
  m4a: ["󰈣", "Purple"],
  m4v: ["󰈫", "Orange"],
  md: ["󰍔", "Grey"],
  mkv: ["󰈫", "Green"],
  mov: ["󰈫", "Cyan"],
  mp3: ["󰈣", "Azure"],
  mp4: ["󰈫", "Azure"],
  mpeg: ["󰈫", "Purple"],
  mpg: ["󰈫", "Purple"],
  ogg: ["󰈣", "Grey"],
  patch: ["󰦓", "Red"],
  php: ["󰌟", "Purple"],
  png: ["󰸭", "Purple"],
  pps: ["󱎐", "Red"],
  ppsm: ["󱎐", "Red"],
  ppsx: ["󱎐", "Red"],
  ppt: ["󱎐", "Red"],
  pptm: ["󱎐", "Red"],
  pptx: ["󱎐", "Red"],
  purs: ["", "Grey"],
  py: ["󰌠", "Yellow"],
  rar: ["󰗄", "Green"],
  rb: ["󰴭", "Red"],
  rpm: ["󰗄", "Red"],
  rs: ["󱘗", "Orange"],
  scss: ["󰟬", "Red"],
  sh: ["", "Green"],
  sit: ["󰗄", "Red"],
  snd: ["󰈣", "Red"],
  sql: ["󰆼", "Grey"],
  tar: ["󰗄", "Cyan"],
  tf: ["󱁢", "Blue"],
  tgz: ["󰗄", "Grey"],
  tif: ["󰈟", "Yellow"],
  tiff: ["󰈟", "Yellow"],
  toml: ["", "Orange"],
  ts: ["󰛦", "Azure"],
  tsx: ["󰛦", "Azure"],
  txt: ["󰦪", "Yellow"],
  txz: ["󰗄", "Purple"],
  vim: ["", "Green"],
  wav: ["󰈣", "Green"],
  webm: ["󰈫", "Grey"],
  webp: ["󰈟", "Blue"],
  wma: ["󰈣", "Blue"],
  wmv: ["󰈫", "Blue"],
  xls: ["󱎏", "Green"],
  xlsm: ["󱎏", "Green"],
  xlsx: ["󱎏", "Green"],
  xlt: ["󱎏", "Green"],
  xltm: ["󱎏", "Green"],
  xltx: ["󱎏", "Green"],
  xml: ["󰗀", "Orange"],
  xz: ["󰗄", "Green"],
  yaml: ["", "Purple"],
  yml: ["", "Purple"],
  z: ["󰗄", "Grey"],
  zip: ["󰗄", "Azure"],
  zsh: ["", "Green"],
  zst: ["󰗄", "Yellow"],
};

const BY_DIRECTORY: Record<string, Entry> = {
  ".cache": ["󰪺", "Cyan"],
  ".config": ["󱁿", "Cyan"],
  ".git": ["", "Orange"],
  ".github": ["", "Azure"],
  ".local": ["󰉌", "Cyan"],
  ".vim": ["󰉋", "Green"],
  AppData: ["󰉌", "Orange"],
  Applications: ["󱧺", "Orange"],
  Desktop: ["󰚝", "Orange"],
  Documents: ["󱧶", "Orange"],
  Downloads: ["󰉍", "Orange"],
  Favorites: ["󱃪", "Orange"],
  Library: ["󰲂", "Orange"],
  Music: ["󱍙", "Orange"],
  Network: ["󰡰", "Orange"],
  Pictures: ["󰉏", "Orange"],
  ProgramData: ["󰉌", "Orange"],
  Public: ["󱧰", "Orange"],
  System: ["󱧼", "Orange"],
  Templates: ["󱋣", "Orange"],
  Trash: ["󱧴", "Orange"],
  Users: ["󰉌", "Orange"],
  Videos: ["󱞊", "Orange"],
  Volumes: ["󰉓", "Orange"],
  autoload: ["󱁽", "Green"],
  bin: ["󱧺", "Yellow"],
  boot: ["󰴋", "Yellow"],
  build: ["󱧼", "Grey"],
  colors: ["󱁽", "Green"],
  compiler: ["󱁽", "Green"],
  dev: ["󱧼", "Yellow"],
  doc: ["󱂷", "Purple"],
  docs: ["󱂷", "Purple"],
  etc: ["󱁿", "Yellow"],
  ftdetect: ["󱁽", "Green"],
  ftplugin: ["󱁽", "Green"],
  home: ["󱂵", "Yellow"],
  indent: ["󱁽", "Green"],
  keymap: ["󱁽", "Green"],
  lang: ["󱁽", "Green"],
  lib: ["󰲂", "Yellow"],
  lsp: ["󱁽", "Green"],
  lua: ["󰉋", "Blue"],
  media: ["󱧺", "Yellow"],
  "mini.nvim": ["󰚝", "Red"],
  mnt: ["󰉓", "Yellow"],
  node_modules: ["", "Green"],
  nvim: ["󰉋", "Green"],
  opt: ["󰉗", "Yellow"],
  pack: ["󱁽", "Green"],
  parser: ["󱁽", "Green"],
  plugin: ["󱁽", "Green"],
  proc: ["󰢬", "Yellow"],
  queries: ["󱁽", "Green"],
  root: ["󰷌", "Yellow"],
  rplugin: ["󱁽", "Green"],
  sbin: ["󱧺", "Yellow"],
  snippets: ["󱁽", "Yellow"],
  spell: ["󱁽", "Green"],
  src: ["󰴉", "Purple"],
  srv: ["󱋣", "Yellow"],
  syntax: ["󱁽", "Green"],
  test: ["󱞊", "Blue"],
  tests: ["󱞊", "Blue"],
  tmp: ["󰪺", "Yellow"],
  tutor: ["󱁽", "Green"],
  usr: ["󰉌", "Yellow"],
  var: ["󱋣", "Yellow"],
};

const DEFAULT_FILE: Entry = ["󰈔", "Grey"];
// neo-tree's own folder glyphs, not mini.icons'.
const DIRECTORY_CLOSED: Entry = ["\ue5ff", "Azure"];
const DIRECTORY_OPEN: Entry = ["\ue5fe", "Azure"];

/** Whether the icon column renders at all. */
export function iconsEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  const flag = env.OMNI_INK_ICONS;
  return !(flag === "0" || flag === "false");
}

/**
 * Icon for a tree row. Resolution order is mini.icons': exact basename,
 * then extension, then the category default.
 */
export function iconFor(
  name: string,
  type: "file" | "directory",
  expanded = false,
): { glyph: string; color: string } {
  if (type === "directory") {
    const named = BY_DIRECTORY[name];
    const entry = named ?? (expanded ? DIRECTORY_OPEN : DIRECTORY_CLOSED);
    return { glyph: entry[0], color: ICON_COLORS[entry[1]] };
  }
  let entry: Entry | undefined = BY_NAME[name];
  if (!entry) {
    const dot = name.lastIndexOf(".");
    if (dot > 0) entry = BY_EXTENSION[name.slice(dot + 1).toLowerCase()];
  }
  entry ??= DEFAULT_FILE;
  return { glyph: entry[0], color: ICON_COLORS[entry[1]] };
}
