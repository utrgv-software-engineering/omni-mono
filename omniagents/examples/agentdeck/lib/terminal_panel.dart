import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:provider/provider.dart';
import 'package:xterm/xterm.dart';

import 'terminal_session.dart';

class TerminalSheet extends StatelessWidget {
  const TerminalSheet({super.key});

  @override
  Widget build(BuildContext context) {
    final deck = context.watch<TerminalDeck>();
    final theme = Theme.of(context);
    final height = MediaQuery.of(context).size.height * 0.7;

    return Align(
      alignment: Alignment.bottomCenter,
      child: Container(
        height: height,
        color: theme.colorScheme.surface,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          child: _TerminalTabs(
            deck: deck,
            onClose: () => Navigator.of(context).pop(),
          ),
        ),
      ),
    );
  }
}

class DockedTerminalPane extends StatelessWidget {
  const DockedTerminalPane({super.key, required this.onClose, this.padding});

  final VoidCallback onClose;
  final EdgeInsetsGeometry? padding;

  @override
  Widget build(BuildContext context) {
    final deck = context.watch<TerminalDeck>();
    final resolvedPadding =
        padding ?? const EdgeInsets.symmetric(horizontal: 16, vertical: 12);
    return Padding(
      padding: resolvedPadding,
      child: _TerminalTabs(
        deck: deck,
        onClose: onClose,
      ),
    );
  }
}

class _TerminalTabs extends StatefulWidget {
  const _TerminalTabs({required this.deck, required this.onClose});

  final TerminalDeck deck;
  final VoidCallback onClose;

  @override
  State<_TerminalTabs> createState() => _TerminalTabsState();
}

class _TerminalTabsState extends State<_TerminalTabs>
    with TickerProviderStateMixin {
  TabController? _controller;

  @override
  void didUpdateWidget(covariant _TerminalTabs oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.deck != widget.deck) {
      _disposeController();
    }
  }

  @override
  void dispose() {
    _disposeController();
    super.dispose();
  }

  void _disposeController() {
    _controller?.removeListener(_handleTabChanged);
    _controller?.dispose();
    _controller = null;
  }

  void _syncController() {
    final length = widget.deck.length;
    if (length == 0) {
      if (_controller != null) {
        _disposeController();
      }
      return;
    }
    if (_controller == null || _controller!.length != length) {
      _disposeController();
      _controller = TabController(length: length, vsync: this);
      _controller!.addListener(_handleTabChanged);
    }
    final desired = widget.deck.activeIndex;
    if (_controller!.index != desired) {
      _controller!.index = desired;
    }
  }

  void _handleTabChanged() {
    final controller = _controller;
    if (controller == null || controller.indexIsChanging) return;
    widget.deck.setActiveIndex(controller.index);
  }

  Future<void> _handleAdd() async {
    await widget.deck.addSession();
  }

  @override
  Widget build(BuildContext context) {
    _syncController();
    final sessions = widget.deck.sessions;
    if (sessions.isEmpty) {
      return _EmptyTerminal(deck: widget.deck, onClose: widget.onClose);
    }
    final theme = Theme.of(context);
    final controller = _controller!;
    return Column(
      children: [
        Material(
          color: theme.colorScheme.surface,
          child: Row(
            children: [
              Expanded(
                child: Align(
                  alignment: Alignment.centerLeft,
                  child: TabBar(
                    controller: controller,
                    isScrollable: true,
                    labelColor: theme.colorScheme.primary,
                    unselectedLabelColor:
                        theme.colorScheme.onSurfaceVariant.withOpacity(0.7),
                    indicatorColor: theme.colorScheme.primary,
                    tabAlignment: TabAlignment.start,
                    padding: EdgeInsets.zero,
                    labelPadding: const EdgeInsets.symmetric(horizontal: 12),
                    onTap: (index) => widget.deck.setActiveIndex(index),
                    tabs: List.generate(sessions.length, (index) {
                      final title = widget.deck.titleFor(index);
                      final selected = widget.deck.activeIndex == index;
                      final color = selected
                          ? theme.colorScheme.primary
                          : theme.colorScheme.onSurfaceVariant.withOpacity(0.7);
                      final canClose = sessions.length > 1;
                      return Tab(
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Text(title),
                            if (canClose) ...[
                              const SizedBox(width: 8),
                              GestureDetector(
                                behavior: HitTestBehavior.opaque,
                                onTap: () {
                                  widget.deck.closeSession(index);
                                },
                                child: Icon(
                                  Icons.close,
                                  size: 14,
                                  color: color,
                                ),
                              ),
                            ],
                          ],
                        ),
                      );
                    }),
                  ),
                ),
              ),
              IconButton(
                icon: const Icon(Icons.add),
                tooltip: 'New Terminal',
                onPressed: _handleAdd,
              ),
              IconButton(
                icon: const Icon(Icons.close),
                tooltip: 'Close',
                onPressed: widget.onClose,
              ),
            ],
          ),
        ),
        const SizedBox(height: 8),
        Expanded(
          child: TabBarView(
            controller: controller,
            physics: const NeverScrollableScrollPhysics(),
            children: List.generate(sessions.length, (index) {
              final session = sessions[index];
              return ChangeNotifierProvider.value(
                value: session,
                child: const _TerminalPane(),
              );
            }),
          ),
        ),
      ],
    );
  }
}

class _EmptyTerminal extends StatelessWidget {
  const _EmptyTerminal({required this.deck, required this.onClose});

  final TerminalDeck deck;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.end,
          children: [
            IconButton(
              icon: const Icon(Icons.add),
              tooltip: 'New Terminal',
              onPressed: () async {
                await deck.addSession();
              },
            ),
            IconButton(
              icon: const Icon(Icons.close),
              tooltip: 'Close',
              onPressed: onClose,
            ),
          ],
        ),
        Expanded(
          child: Center(
            child: OutlinedButton.icon(
              icon: const Icon(Icons.add),
              label: const Text('Open Terminal'),
              onPressed: () async {
                await deck.addSession();
              },
            ),
          ),
        ),
      ],
    );
  }
}

class _TerminalPane extends StatelessWidget {
  const _TerminalPane();

  @override
  Widget build(BuildContext context) {
    final session = context.watch<RemoteTerminalSession>();
    final theme = Theme.of(context);
    final terminal = session.terminal;
    final controller = session.controller;

    return DecoratedBox(
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceVariant,
        border: Border.all(
          color: theme.colorScheme.outline.withOpacity(0.3),
        ),
      ),
      child: terminal != null && controller != null
          ? TerminalView(
              terminal,
              controller: controller,
              autofocus: true,
              backgroundOpacity: 1.0,
              onKeyEvent: (node, event) {
                if (!kIsWeb ||
                    event is! KeyUpEvent ||
                    event.logicalKey != LogicalKeyboardKey.space) {
                  return KeyEventResult.ignored;
                }
                terminal.textInput(' ');
                return KeyEventResult.handled;
              },
              textStyle: TerminalStyle(
                fontFamily: GoogleFonts.robotoMono().fontFamily!,
                fontSize: 14,
              ),
            )
          : _terminalPlaceholder(session, theme),
    );
  }
}

Widget _terminalPlaceholder(RemoteTerminalSession session, ThemeData theme) {
  if (session.error != null) {
    return Center(
      child: Text(
        'Unable to open terminal.',
        style: theme.textTheme.bodyMedium,
      ),
    );
  }
  if (session.connecting) {
    return Center(
      child: SizedBox(
        width: 32,
        height: 32,
        child: CircularProgressIndicator(
          strokeWidth: 3,
          valueColor: AlwaysStoppedAnimation<Color>(
            theme.colorScheme.primary,
          ),
        ),
      ),
    );
  }
  if (session.exited) {
    return Center(
      child: Text(
        'Terminal exited.',
        style: theme.textTheme.bodyMedium,
      ),
    );
  }
  return const SizedBox.shrink();
}
