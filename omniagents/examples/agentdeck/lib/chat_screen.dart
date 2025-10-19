import 'package:flutter/material.dart';
import 'dart:async';
import 'package:provider/provider.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:flutter/services.dart';
import 'dart:math' as math;
import 'dart:convert';

import 'models.dart';
import 'conversation_controller.dart';
import 'remote_agent_client.dart';
import 'theme/app_colors.dart';
import 'folder_picker.dart';
import 'terminal_session.dart';
import 'terminal_panel.dart';

class _SendMessageIntent extends Intent {
  const _SendMessageIntent();
}

class _InsertNewlineIntent extends Intent {
  const _InsertNewlineIntent();
}

/// Simple chat screen that talks to a remote OmniAgents server.
class ChatScreen extends StatelessWidget {
  const ChatScreen({
    required this.controller,
    required this.title,
    this.onClose,
    this.onRunStarted,
    this.embedded = false,
    this.model,
    this.dragIndex,
    this.dockTerminals = false,
    super.key,
  });

  final ConversationController controller;
  final String title;
  final VoidCallback? onClose;
  final void Function(String?, String?)? onRunStarted;
  final bool embedded;

  /// If provided, this existing [ChatModel] instance will be reused instead of
  /// creating a new one.  This prevents state loss when widgets are rebuilt
  /// (e.g., during layout changes).
  final ChatModel? model;

  /// Index of this column inside a ReorderableListView.  When provided, only
  /// the header bar becomes the drag handle so that the timeline remains
  /// scrollable.
  final int? dragIndex;
  final bool dockTerminals;

  @override
  Widget build(BuildContext context) {
    if (model != null) {
      return ChangeNotifierProvider.value(
        value: model!,
        child: _ChatView(
          title: title,
          onClose: onClose,
          embedded: embedded,
          dragIndex: dragIndex,
          dockTerminals: dockTerminals,
        ),
      );
    }

    return ChangeNotifierProvider(
      create: (_) => ChatModel(controller,
          sessionId: controller.sessionId, onRunStarted: onRunStarted),
      child: _ChatView(
        title: title,
        onClose: onClose,
        embedded: embedded,
        dragIndex: dragIndex,
        dockTerminals: dockTerminals,
      ),
    );
  }
}

class _ChatView extends StatefulWidget {
  const _ChatView(
      {required this.title,
      this.onClose,
      this.embedded = false,
      this.dragIndex,
      this.dockTerminals = false});

  final String title;
  final VoidCallback? onClose;
  final bool embedded;
  final int? dragIndex;
  final bool dockTerminals;

  @override
  State<_ChatView> createState() => _ChatViewState();
}

class _StatusData {
  const _StatusData({required this.text, required this.showSpinner});

  final String text;
  final bool showSpinner;
}

class _ChatViewState extends State<_ChatView> {
  final _scrollController = ScrollController();
  final _inputController = TextEditingController();
  final _inputFocusNode = FocusNode();

  // Tracks the number of chat items we have already rendered so that we only
  // scroll to the bottom when new items arrive, not on every rebuild (e.g.
  // when the user simply taps inside the column).
  int _lastItemCount = 0;
  String? _lastStatusSignature;

  @override
  void initState() {
    super.initState();
    RawKeyboard.instance.addListener(_handleRawKey);
  }

  @override
  void dispose() {
    RawKeyboard.instance.removeListener(_handleRawKey);
    _scrollController.dispose();
    _inputController.dispose();
    _inputFocusNode.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final model = context.watch<ChatModel>();
    final status = _statusData(model);
    final hasStatus = status != null;
    final totalCount = model.items.length + (hasStatus ? 1 : 0);
    final statusSignature =
        status == null ? null : '${status.text}::${status.showSpinner}';
    final usageButtonAppBar = _buildUsageButton(model, compact: true);

    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients &&
          (totalCount != _lastItemCount ||
              statusSignature != _lastStatusSignature)) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
        );
        _lastItemCount = totalCount;
        _lastStatusSignature = statusSignature;
      }
    });

    // Wrap the entire chat column in a SafeArea so the bottom system insets
    // (e.g. iOS home-indicator) never obscure the input row.  We disable the
    // other insets because the header/app-bar already handles the top and the
    // horizontal padding is managed elsewhere.
    final content = SafeArea(
      top: widget.embedded,
      bottom: true,
      left: true,
      right: true,
      maintainBottomViewPadding: true,
      child: Column(
        children: [
          // Header (only for embedded mode)
          if (widget.embedded) _buildHeader(model),
          Expanded(
            child: ListView.builder(
              controller: _scrollController,
              itemCount: totalCount,
              itemBuilder: (context, index) {
                if (index < model.items.length) {
                  final item = model.items[index];
                  return _buildItem(item);
                }
                if (status != null) {
                  return _buildStatusRow(status);
                }
                return const SizedBox.shrink();
              },
            ),
          ),
          _buildInputRow(model),
        ],
      ),
    );

    if (widget.embedded) {
      return content;
    }

    return Scaffold(
      appBar: AppBar(
        title: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Flexible(child: _buildTitleWithCwd(model, appBar: true)),
            if (usageButtonAppBar != null) ...[
              const SizedBox(width: 8),
              usageButtonAppBar,
            ],
          ],
        ),
        bottom: const PreferredSize(
          preferredSize: Size.fromHeight(1),
          child: Divider(height: 1, thickness: 1, color: bgCardAlt),
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.terminal),
            tooltip: 'Open Terminal',
            onPressed:
                model.client.hasSession ? () => _openTerminal(model) : null,
          ),
          const SizedBox(width: 8),
          IconButton(
            icon: const Icon(Icons.folder_open),
            tooltip: 'Open Folder',
            onPressed:
                model.client.hasSession ? null : () => _openFolderPicker(model),
          ),
          if (widget.onClose != null) ...[
            const SizedBox(width: 8),
            IconButton(
              icon: const Icon(Icons.close),
              tooltip: 'Close',
              onPressed: widget.onClose,
            ),
          ],
        ],
      ),
      body: content,
    );
  }

  Widget _buildTitleWithCwd(ChatModel model, {bool appBar = false}) {
    final label = model.cwdBase;
    if (appBar) {
      final subtle = TextStyle(
        color: Theme.of(context).colorScheme.onSurfaceVariant.withOpacity(0.7),
        fontWeight: FontWeight.w400,
      );
      final appBarStyle = Theme.of(context).appBarTheme.titleTextStyle ??
          Theme.of(context)
              .textTheme
              .titleLarge
              ?.copyWith(color: Theme.of(context).colorScheme.onSurface);
      return Text.rich(
        TextSpan(
          text: widget.title,
          children: [
            if (label != null && label.isNotEmpty)
              TextSpan(text: ' (' + label + ')', style: subtle),
          ],
        ),
        overflow: TextOverflow.ellipsis,
        style: appBarStyle,
      );
    } else {
      final base = TextStyle(
        fontWeight: FontWeight.bold,
        fontSize: 14,
        color: Theme.of(context).colorScheme.onSurface,
      );
      final subtle = TextStyle(
        color: Theme.of(context).colorScheme.onSurfaceVariant.withOpacity(0.7),
        fontWeight: FontWeight.w400,
        fontSize: (base.fontSize ?? 14) * 0.9,
      );
      return RichText(
        overflow: TextOverflow.ellipsis,
        text: TextSpan(
          style: base,
          children: [
            TextSpan(text: widget.title),
            if (label != null && label.isNotEmpty)
              TextSpan(text: ' (' + label + ')', style: subtle),
          ],
        ),
      );
    }
  }

  Widget _buildStatusRow(_StatusData status) {
    final style = Theme.of(context).textTheme.bodySmall;
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 4, 12, 8),
      child: Row(
        children: [
          if (status.showSpinner)
            SizedBox(
              width: 14,
              height: 14,
              child: CircularProgressIndicator(
                strokeWidth: 2,
                valueColor: AlwaysStoppedAnimation<Color>(
                    Theme.of(context).colorScheme.primary),
              ),
            )
          else
            const SizedBox(width: 14, height: 14),
          const SizedBox(width: 8),
          Expanded(
            child: Text(status.text, style: style),
          ),
        ],
      ),
    );
  }

  _StatusData? _statusData(ChatModel model) {
    String? text;
    var showSpinner = false;
    if (model.statusText != null && model.statusText!.trim().isNotEmpty) {
      text = model.statusText!.trim();
      showSpinner = model.statusShowSpinner;
    } else if (model.isStreaming) {
      text = model.currentRunId != null && model.currentRunId!.isNotEmpty
          ? 'Thinking…'
          : 'Thinking…';
      showSpinner = true;
    }
    if (text == null) return null;
    return _StatusData(text: text, showSpinner: showSpinner);
  }

  Widget _buildInputRow(ChatModel model) {
    final screenHeight = MediaQuery.of(context).size.height;
    final maxInputHeight = screenHeight * 0.5;
    return SafeArea(
      top: false,
      maintainBottomViewPadding: true,
      minimum: const EdgeInsets.fromLTRB(12, 8, 12, 16),
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: maxInputHeight),
        child: Shortcuts(
          shortcuts: <LogicalKeySet, Intent>{
            LogicalKeySet(LogicalKeyboardKey.enter): const _SendMessageIntent(),
            LogicalKeySet(LogicalKeyboardKey.numpadEnter):
                const _SendMessageIntent(),
            LogicalKeySet(LogicalKeyboardKey.alt, LogicalKeyboardKey.enter):
                const _InsertNewlineIntent(),
            LogicalKeySet(LogicalKeyboardKey.shift, LogicalKeyboardKey.enter):
                const _InsertNewlineIntent(),
            LogicalKeySet(LogicalKeyboardKey.control, LogicalKeyboardKey.keyJ):
                const _InsertNewlineIntent(),
          },
          child: Actions(
            actions: <Type, Action<Intent>>{
              _SendMessageIntent: CallbackAction<_SendMessageIntent>(
                onInvoke: (intent) {
                  if (!_inputFocusNode.hasFocus) return null;
                  _send(model);
                  FocusScope.of(context).requestFocus(_inputFocusNode);
                  return null;
                },
              ),
              _InsertNewlineIntent: CallbackAction<_InsertNewlineIntent>(
                onInvoke: (intent) {
                  if (!_inputFocusNode.hasFocus) return null;
                  _insertNewlineAtCursor();
                  FocusScope.of(context).requestFocus(_inputFocusNode);
                  return null;
                },
              ),
            },
            child: TextField(
              controller: _inputController,
              focusNode: _inputFocusNode,
              minLines: 1,
              maxLines: null,
              keyboardType: TextInputType.multiline,
              autocorrect: false,
              enableSuggestions: false,
              smartDashesType: SmartDashesType.disabled,
              smartQuotesType: SmartQuotesType.disabled,
              textCapitalization: TextCapitalization.none,
              scrollPhysics: const AlwaysScrollableScrollPhysics(),
              textInputAction: TextInputAction.send,
              onSubmitted: (_) => _send(model),
              decoration: InputDecoration(
                hintText: 'Type a message…',
                filled: true,
                fillColor: Theme.of(context).colorScheme.surfaceVariant,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(24),
                  borderSide: BorderSide.none,
                ),
                contentPadding:
                    const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                suffixIcon: model.isStreaming && (model.currentRunId != null)
                    ? IconButton(
                        tooltip: 'Stop',
                        icon: const Icon(Icons.stop_circle_outlined),
                        color: Theme.of(context).colorScheme.error,
                        onPressed: () => model.cancelActiveRun(),
                      )
                    : IconButton(
                        tooltip: 'Send',
                        icon: const Icon(Icons.send),
                        onPressed:
                            model.isStreaming ? null : () => _send(model),
                      ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  void _handleRawKey(RawKeyEvent event) {
    if (event is! RawKeyDownEvent) return;
    if (!_inputFocusNode.hasFocus) return;
    final isUp = event.logicalKey == LogicalKeyboardKey.arrowUp;
    final isDown = event.logicalKey == LogicalKeyboardKey.arrowDown;
    if (!isUp && !isDown) return;
    final model = context.read<ChatModel>();
    final sel = _inputController.selection;
    final text = _inputController.text;
    if (isUp && sel.baseOffset == 0 && sel.extentOffset == 0) {
      final replacement = model.historyUp(text);
      if (replacement != null) {
        _inputController.text = replacement;
        _inputController.selection = TextSelection.fromPosition(
            TextPosition(offset: _inputController.text.length));
      }
    } else if (isDown &&
        sel.baseOffset == text.length &&
        sel.extentOffset == text.length) {
      final replacement = model.historyDown();
      if (replacement != null) {
        _inputController.text = replacement;
        _inputController.selection = TextSelection.fromPosition(
            TextPosition(offset: _inputController.text.length));
      }
    }
  }

  void _insertNewlineAtCursor() {
    final text = _inputController.text;
    final sel = _inputController.selection;
    final start = sel.start.clamp(0, text.length);
    final end = sel.end.clamp(0, text.length);
    final newText = text.replaceRange(start, end, '\n');
    _inputController.text = newText;
    final pos = start + 1;
    _inputController.selection =
        TextSelection.fromPosition(TextPosition(offset: pos));
  }

  void _send(ChatModel model) {
    if (model.isStreaming) return;
    final text = _inputController.text.trim();
    if (text.isEmpty) return;
    _inputController.clear();
    FocusScope.of(context).requestFocus(_inputFocusNode);
    debugPrint('[chat] send "$text"');
    model.send(text);
  }

  String? _usageTooltip(ChatModel model) {
    final name = model.modelName;
    final cw = model.contextWindow;
    final totals = model.usageTotals;
    final used = totals?.totalTokens;
    if (name != null &&
        name.isNotEmpty &&
        cw != null &&
        cw > 0 &&
        used != null) {
      var remaining = cw - used;
      if (remaining < 0) remaining = 0;
      var pct = ((remaining * 100) / cw).floor();
      if (pct < 0) pct = 0;
      if (pct > 100) pct = 100;
      return 'context remaining: $pct% • $name';
    }
    return null;
  }

  Widget? _buildUsageButton(ChatModel model, {required bool compact}) {
    final message = _usageTooltip(model);
    if (message == null) return null;
    final color = Theme.of(context).colorScheme.onSurfaceVariant;
    final constraints = compact
        ? const BoxConstraints(minWidth: 32, minHeight: 32)
        : const BoxConstraints(minWidth: 36, minHeight: 36);
    final splashRadius = compact ? 18.0 : 20.0;
    return Tooltip(
      message: message,
      child: IconButton(
        onPressed: () => _showUsageDetails(message),
        padding: EdgeInsets.zero,
        constraints: constraints,
        splashRadius: splashRadius,
        iconSize: 20,
        visualDensity: VisualDensity.compact,
        icon: Icon(
          Icons.info_outline,
          size: 20,
          color: color,
        ),
      ),
    );
  }

  Widget _headerIconButton({
    required IconData icon,
    required String tooltip,
    required VoidCallback? onPressed,
  }) {
    return Tooltip(
      message: tooltip,
      child: IconButton(
        onPressed: onPressed,
        padding: EdgeInsets.zero,
        constraints: const BoxConstraints(minWidth: 32, minHeight: 32),
        splashRadius: 18,
        iconSize: 20,
        visualDensity: VisualDensity.compact,
        icon: Icon(icon, size: 20),
      ),
    );
  }

  Future<void> _showUsageDetails(String message) async {
    if (!mounted) return;
    await showDialog<void>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Context usage'),
        content: Text(message),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(),
            child: const Text('Close'),
          ),
        ],
      ),
    );
  }

  Widget _buildItem(ChatItem item) {
    final scheme = Theme.of(context).colorScheme;
    if (item is UserMessage) {
      return _bubble(item.text, isUser: true, bubbleColor: scheme.primary);
    } else if (item is AssistantMessage) {
      return _assistantMessage(item.text);
    } else if (item is ToolCallCard) {
      return _toolCallCard(item.tool, item.arguments);
    } else if (item is ToolResultCard) {
      return _toolResultCard(item.tool, item.output);
    } else if (item is ApprovalRequestCard) {
      return _approvalRequestCard(context, item);
    } else if (item is ErrorBanner) {
      return Padding(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
        child: ListTile(
          tileColor: errorRed.withOpacity(0.15),
          title: Text(item.message),
        ),
      );
    } else if (item is ToolInteractionCard) {
      return _toolInteractionCard(item.tool, item.input, item.output,
          metadata: item.metadata, running: item.running);
    }
    return const SizedBox.shrink();
  }

  // Helpers -----------------------------------------------------------------

  Widget _assistantMessage(String text) {
    final onColor = Theme.of(context).colorScheme.onSurface;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      child: MarkdownBody(
        data: text,
        selectable: false,
        styleSheet: MarkdownStyleSheet.fromTheme(Theme.of(context))
            .copyWith(p: TextStyle(color: onColor)),
      ),
    );
  }

  Widget _bubble(String text,
      {required bool isUser,
      required Color bubbleColor,
      bool useMarkdown = false}) {
    final isTransparent = bubbleColor.alpha == 0;
    final onColor = isTransparent
        ? Theme.of(context).colorScheme.onSurface
        : (ThemeData.estimateBrightnessForColor(bubbleColor) == Brightness.dark
            ? Colors.white
            : Colors.black87);

    final bubble = LayoutBuilder(
      builder: (context, constraints) {
        // Use the width that is really available for this bubble rather than
        // the full screen width.  The constraints already account for the
        // column width as well as the surrounding 8 px horizontal padding
        // applied in the widget tree above.  We subtract a tiny margin so the
        // bubble never touches the column edge and to avoid rare rounding
        // errors that could otherwise lead to a 1 px overflow warning.
        final available = constraints.maxWidth;
        // Leave a 4 px safety gap to be extra safe on high-DPI screens.
        final maxWidth = math.min(math.max(0.0, available - 4), 404.0);
        return Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
          decoration: isTransparent
              ? null
              : BoxDecoration(
                  color: bubbleColor,
                  borderRadius: BorderRadius.circular(18),
                ),
          constraints: BoxConstraints(maxWidth: maxWidth),
          child: DefaultTextStyle(
            style: TextStyle(color: onColor),
            child: useMarkdown
                ? MarkdownBody(
                    data: text,
                    selectable: false,
                    styleSheet: MarkdownStyleSheet.fromTheme(Theme.of(context))
                        .copyWith(p: TextStyle(color: onColor)),
                  )
                : SelectableText(text),
          ),
        );
      },
    );

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
      child: Row(
        mainAxisAlignment:
            isUser ? MainAxisAlignment.end : MainAxisAlignment.start,
        children: [Flexible(child: bubble)],
      ),
    );
  }

  // Attempts to pretty-print JSON; falls back to markdown text.
  Widget _toolCallCard(String tool, String args) {
    final scheme = Theme.of(context).colorScheme;

    String? bodyText;
    Widget? bodyWidget;

    if (args.isNotEmpty) {
      dynamic parsed;
      try {
        parsed = jsonDecode(args);
      } catch (_) {}

      if (parsed is Map<String, dynamic>) {
        bodyWidget = _jsonKeyValueWidget(parsed);
      } else {
        bodyText = '```text\n$args\n```';
      }
    }

    return _CollapsibleCard(
      iconColor: scheme.primary,
      icon: Icons.build,
      title: 'Using $tool',
      body: bodyText,
      bodyWidget: bodyWidget,
      showIcon: false,
    );
  }

  Widget _toolResultCard(String tool, String output) {
    String? bodyText;
    Widget? bodyWidget;
    if (output.isNotEmpty) {
      dynamic parsed;
      try {
        parsed = jsonDecode(output);
      } catch (_) {}

      if (parsed is Map<String, dynamic>) {
        bodyWidget = _jsonKeyValueWidget(parsed);
      } else {
        bodyText = '```text\n$output\n```';
      }
    }

    return _CollapsibleCard(
      iconColor: successGreen,
      icon: Icons.check,
      title: 'Output from $tool',
      body: bodyText,
      bodyWidget: bodyWidget,
      initiallyExpanded: false,
      showIcon: false,
    );
  }

  Widget _approvalRequestCard(BuildContext context, ApprovalRequestCard item) {
    // Use the same visual language as the other tool cards for consistency.
    const iconColor = warningOrange;
    final scheme = Theme.of(context).colorScheme;

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
      child: Container(
        decoration: BoxDecoration(
          color: scheme.surfaceVariant,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: iconColor.withOpacity(0.12)),
        ),
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Re-use the same circular icon badge as other tool cards.
                Container(
                  width: 20,
                  height: 20,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: Colors.transparent,
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(
                      color: iconColor.withOpacity(0.8),
                      width: 1.2,
                    ),
                  ),
                  child: Icon(
                    Icons.verified_user,
                    size: 12,
                    color: iconColor.withOpacity(0.9),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('Approve ${item.tool}',
                          style: const TextStyle(
                              fontWeight: FontWeight.w500, fontSize: 13)),
                      const SizedBox(height: 2),
                      if (item.metadata != null &&
                          _hasRichDisplay(item.metadata))
                        _richApprovalContent(item.metadata!, item.arguments)
                      else
                        Text(
                            item.arguments.isEmpty
                                ? 'No parameters'
                                : item.arguments,
                            style: const TextStyle(fontSize: 12)),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                TextButton(
                  onPressed: () => item.onDecision(false, false),
                  child: const Text('Reject'),
                ),
                TextButton(
                  onPressed: () => item.onDecision(true, true),
                  child: const Text('Always'),
                ),
                ElevatedButton(
                  style: ElevatedButton.styleFrom(
                      backgroundColor: iconColor,
                      foregroundColor: Colors.white),
                  onPressed: () => item.onDecision(true, false),
                  child: const Text('Approve Once'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _toolInteractionCard(String tool, String input, String? output,
      {Map<String, dynamic>? metadata, bool running = false}) {
    final hasResult = output != null && output.isNotEmpty;
    final iconColor =
        hasResult ? successGreen : Theme.of(context).colorScheme.primary;
    final icon = hasResult ? Icons.check : Icons.timelapse;

    // Build a richer body with JSON aware formatting.
    List<Widget> children = [];

    void addSection(String label, String content, Color color) {
      dynamic parsed;
      try {
        parsed = jsonDecode(content);
      } catch (_) {}

      children.add(Text(label,
          style: TextStyle(
              fontWeight: FontWeight.w500, fontSize: 12, color: color)));
      children.add(const SizedBox(height: 4));
      if (parsed is Map<String, dynamic>) {
        children.add(_jsonKeyValueWidget(parsed));
      } else {
        final fenced = '```text\n$content\n```';
        children.add(MarkdownBody(
          data: fenced,
          selectable: false,
          styleSheet: MarkdownStyleSheet.fromTheme(Theme.of(context))
              .copyWith(p: const TextStyle(fontSize: 12)),
        ));
      }
      children.add(const SizedBox(height: 12));
    }

    // Input is shown in the header preview now; omit detailed input section.

    if (hasResult) {
      if (metadata != null && _hasRichDisplay(metadata)) {
        children.add(_richToolOutput(metadata, output));
      } else {
        addSection('output', output, successGreen);
      }
    }

    if (children.isNotEmpty) {
      // Remove last spacer
      if (children.last is SizedBox) children.removeLast();
      final header = _toolHeader(tool, input, metadata);
      final t = running ? '$header • running…' : header;
      return _CollapsibleCard(
        iconColor: iconColor,
        icon: icon,
        title: t,
        body: null,
        bodyWidget: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: children,
        ),
        initiallyExpanded: false,
        showIcon: false,
      );
    }
    return const SizedBox.shrink();
  }

  String _toolHeader(String tool, String args, Map<String, dynamic>? metadata) {
    final summary = metadata != null ? (metadata['summary'] as String?) : null;
    if (summary != null && summary.trim().isNotEmpty) {
      return summary.trim();
    }
    return _buildToolHeader(tool, args);
  }

  String _buildToolHeader(String tool, String args) {
    final preview = _formatToolArgsPreview(args, 80);
    if (preview.isEmpty) return tool;
    return '$tool($preview)';
  }

  String _formatToolArgsPreview(String args, int maxLen) {
    if (args.isEmpty) return '';
    dynamic parsed;
    try {
      parsed = jsonDecode(args);
    } catch (_) {}

    String text;
    if (parsed is Map<String, dynamic>) {
      final parts = <String>[];
      parsed.forEach((k, v) {
        String vs;
        if (v is String) {
          vs = '"' + v + '"';
        } else if (v is num || v is bool) {
          vs = v.toString();
        } else {
          try {
            vs = jsonEncode(v);
          } catch (_) {
            vs = v.toString();
          }
        }
        parts.add('$k: $vs');
      });
      text = parts.join(', ');
    } else {
      text = args.replaceAll(RegExp(r'\s+'), ' ').trim();
    }

    if (text.length > maxLen) {
      return text.substring(0, maxLen - 3) + '...';
    }
    return text;
  }

  bool _hasRichDisplay(Map<String, dynamic>? metadata) {
    if (metadata == null) return false;
    final dt = metadata['display_type'];
    return dt is String && dt.isNotEmpty;
  }

  Widget _richToolOutput(Map<String, dynamic> metadata, String? plainOutput) {
    final display = metadata['display_type'] as String?;
    if (display == 'diff') {
      final value = metadata['value'];
      List<String> lines = [];
      if (value is Map && value['diff_lines'] is List) {
        for (final v in (value['diff_lines'] as List)) {
          if (v is String) lines.add(v);
        }
      }
      return _diffView(lines);
    }
    if (display == 'table') {
      final table = metadata['table'];
      if (table is Map) {
        final cols = <String>[];
        if (table['columns'] is List) {
          for (final c in (table['columns'] as List)) {
            if (c is Map && c['title'] is String)
              cols.add(c['title'] as String);
          }
        }
        final rows = <List<String>>[];
        if (table['rows'] is List) {
          for (final r in (table['rows'] as List)) {
            if (r is List) {
              rows.add(r.map((e) => e?.toString() ?? '').toList());
            }
          }
        }
        return _simpleTable(cols, rows);
      }
    }
    if (display == 'file_write') {
      final value = metadata['value'];
      final content = value is String ? value : (plainOutput ?? '');
      return _codeBlock(content);
    }
    if (display == 'command') {
      final meta = metadata['metadata'];
      String command = plainOutput ?? '';
      if (meta is Map && meta['command'] is String) {
        command = meta['command'] as String;
      }
      String output = '';
      final value = metadata['value'];
      if (value is String && value.trim().isNotEmpty) {
        output = value;
      } else {
        final preview = metadata['preview'];
        if (preview is String && preview.trim().isNotEmpty) {
          output = preview;
        } else if (meta is Map) {
          final stdout = meta['stdout'];
          final stderr = meta['stderr'];
          final buffer = StringBuffer();
          bool hasContent = false;
          if (stdout is String && stdout.trim().isNotEmpty) {
            buffer.write(stdout);
            hasContent = true;
          }
          if (stderr is String && stderr.trim().isNotEmpty) {
            if (hasContent) buffer.write('\n');
            buffer.write('[stderr]\n$stderr');
            hasContent = true;
          }
          if (hasContent) {
            output = buffer.toString();
          }
        }
        if (output.isEmpty &&
            plainOutput != null &&
            plainOutput.trim().isNotEmpty) {
          output = plainOutput;
        }
      }
      int? exitCode;
      bool? success;
      bool hasStderr = false;
      if (meta is Map) {
        if (meta['exit_code'] is int) exitCode = meta['exit_code'] as int;
        if (meta['success'] is bool) success = meta['success'] as bool;
        if (meta['has_stderr'] is bool) hasStderr = meta['has_stderr'] as bool;
      }
      final children = <Widget>[
        _codeInline('4 $command'),
      ];
      final statusParts = <String>[];
      if (success != null) {
        statusParts.add(success ? 'success' : 'failed');
      }
      if (exitCode != null) {
        statusParts.add('exit $exitCode');
      }
      if (hasStderr &&
          meta is Map &&
          meta['stderr'] is String &&
          (meta['stderr'] as String).trim().isNotEmpty) {
        statusParts.add('stderr captured');
      }
      if (statusParts.isNotEmpty) {
        children.add(const SizedBox(height: 6));
        final tone =
            Theme.of(context).colorScheme.onSurfaceVariant.withOpacity(0.8);
        children.add(Text(statusParts.join(' • '),
            style: TextStyle(fontSize: 12, color: tone)));
      }
      if (output.trim().isNotEmpty) {
        children.add(const SizedBox(height: 8));
        children.add(_preText(output.trimRight()));
      }
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: children,
      );
    }
    final prev = metadata['preview'];
    if (prev is String && prev.isNotEmpty) {
      return _preText(prev);
    }
    return _preText(plainOutput ?? '');
  }

  Widget _richApprovalContent(
      Map<String, dynamic> metadata, String fallbackArgs) {
    final display = metadata['display_type'] as String?;
    if (display == 'diff') {
      final value = metadata['value'];
      List<String> lines = [];
      if (value is Map && value['diff_lines'] is List) {
        for (final v in (value['diff_lines'] as List)) {
          if (v is String) lines.add(v);
        }
      }
      return _diffView(lines);
    }
    if (display == 'file_write') {
      final value = metadata['value'];
      final content = value is String ? value : '';
      return _codeBlock(content.isEmpty ? fallbackArgs : content);
    }
    if (display == 'command') {
      final meta = metadata['metadata'];
      final cmd = meta is Map && meta['command'] is String
          ? meta['command'] as String
          : fallbackArgs;
      return _codeInline('4 $cmd');
    }
    final prev = metadata['preview'];
    if (prev is String && prev.isNotEmpty) {
      return _preText(prev);
    }
    return Text(fallbackArgs.isEmpty ? 'No parameters' : fallbackArgs,
        style: const TextStyle(fontSize: 12));
  }

  Widget _diffView(List<String> lines) {
    final styleAdd = TextStyle(color: successGreen, fontFamily: 'monospace');
    final styleDel = TextStyle(color: errorRed, fontFamily: 'monospace');
    final styleCtx = TextStyle(
        color: Theme.of(context).colorScheme.onSurfaceVariant,
        fontFamily: 'monospace');
    final children = <Widget>[];
    for (final l in lines) {
      if (l.startsWith('+') && !l.startsWith('+++')) {
        children.add(Text(l, style: styleAdd));
      } else if (l.startsWith('-') && !l.startsWith('---')) {
        children.add(Text(l, style: styleDel));
      } else {
        children.add(Text(l, style: styleCtx));
      }
    }
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(8),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surface,
        border: Border.all(
            color: Theme.of(context)
                .colorScheme
                .onSurfaceVariant
                .withOpacity(0.12)),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: children,
      ),
    );
  }

  Widget _codeBlock(String content) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(8),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surface,
        border: Border.all(
            color: Theme.of(context)
                .colorScheme
                .onSurfaceVariant
                .withOpacity(0.12)),
        borderRadius: BorderRadius.circular(6),
      ),
      child: SelectableText(content,
          style: const TextStyle(fontFamily: 'monospace', fontSize: 12)),
    );
  }

  Widget _codeInline(String content) {
    var cleaned = String.fromCharCodes(content.runes.where((r) => r >= 32));
    if (cleaned.startsWith('4 ')) {
      cleaned = cleaned.substring(2);
    }
    final finalText = '\$ ' + cleaned;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surface,
        border: Border.all(
            color: Theme.of(context)
                .colorScheme
                .onSurfaceVariant
                .withOpacity(0.12)),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Text(finalText,
          style: const TextStyle(fontFamily: 'monospace', fontSize: 12)),
    );
  }

  Widget _preText(String content) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(8),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surface,
        border: Border.all(
            color: Theme.of(context)
                .colorScheme
                .onSurfaceVariant
                .withOpacity(0.12)),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Text(content,
          style: const TextStyle(fontFamily: 'monospace', fontSize: 12)),
    );
  }

  Widget _simpleTable(List<String> columns, List<List<String>> rows) {
    final headers = columns
        .map((c) => DataColumn(
            label:
                Text(c, style: const TextStyle(fontWeight: FontWeight.w600))))
        .toList();
    final tableRows = rows
        .map((r) => DataRow(cells: [
              for (final cell in r)
                DataCell(SingleChildScrollView(
                    scrollDirection: Axis.horizontal,
                    child: Text(cell, style: const TextStyle(fontSize: 12))))
            ]))
        .toList();
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: DataTable(columns: headers, rows: tableRows),
    );
  }

  // -----------------------------------------------------------------------
  // Helper that renders a JSON map as key-value sections with Markdown values
  // -----------------------------------------------------------------------

  Widget _jsonKeyValueWidget(Map<String, dynamic> map) {
    final List<Widget> children = [];
    map.forEach((key, value) {
      // Convert non-string values to pretty JSON strings.
      String valueString;
      if (value is String) {
        valueString = value;
      } else {
        try {
          valueString = const JsonEncoder.withIndent('  ').convert(value);
        } catch (_) {
          valueString = value.toString();
        }
      }

      // Wrap inside a fenced code block so Markdown renders it in a mono font.
      valueString = '```text\n$valueString\n```';

      children.add(Text(key,
          style: TextStyle(
              fontWeight: FontWeight.w500,
              fontSize: 12,
              color: Theme.of(context).colorScheme.onSurfaceVariant)));
      children.add(const SizedBox(height: 4));
      children.add(MarkdownBody(
        data: valueString,
        selectable: false,
        styleSheet: MarkdownStyleSheet.fromTheme(Theme.of(context))
            .copyWith(p: const TextStyle(fontSize: 12)),
      ));
      children.add(const SizedBox(height: 12));
    });

    if (children.isNotEmpty && children.last is SizedBox) {
      children.removeLast();
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: children,
    );
  }

  // -----------------------------------------------------------------------
  // Collapsible card widget -------------------------------------------------
  // -----------------------------------------------------------------------

  Widget _CollapsibleCard({
    required Color iconColor,
    required IconData icon,
    required String title,
    String? body,
    Widget? bodyWidget,
    bool initiallyExpanded = false,
    bool showIcon = true,
  }) {
    return _InnerCollapsibleCard(
      iconColor: iconColor,
      icon: icon,
      title: title,
      body: body,
      bodyWidget: bodyWidget,
      initiallyExpanded: initiallyExpanded,
      showIcon: showIcon,
    );
  }

  // Helper collapsible card widget removed to top-level below

  // Builds the header bar for embedded mode and wraps it in a drag handle
  // when [widget.dragIndex] is provided.
  Widget _buildHeader(ChatModel model) {
    // Build the optional drag-handle (only shown when we are inside a
    // ReorderableListView).
    Widget? dragHandle;
    if (widget.dragIndex != null) {
      dragHandle = ReorderableDragStartListener(
        index: widget.dragIndex!,
        child: Padding(
          padding: const EdgeInsets.only(right: 8),
          child: ConstrainedBox(
            constraints: const BoxConstraints(minWidth: 24, minHeight: 48),
            child: Center(
              child: Icon(
                Icons.drag_indicator,
                size: 20,
                color: Theme.of(context).colorScheme.onSurfaceVariant,
              ),
            ),
          ),
        ),
      );
    }

    final usageButtonHeader = _buildUsageButton(model, compact: true);

    final headerActionButtons = <Widget>[
      _headerIconButton(
        icon: Icons.terminal,
        tooltip: 'Open Terminal',
        onPressed:
            model.client.hasSession ? () => _openTerminal(model) : null,
      ),
      _headerIconButton(
        icon: Icons.folder_open,
        tooltip: 'Open Folder',
        onPressed:
            model.client.hasSession ? null : () => _openFolderPicker(model),
      ),
    ];

    if (widget.onClose != null) {
      headerActionButtons.add(
        _headerIconButton(
          icon: Icons.close,
          tooltip: 'Close',
          onPressed: widget.onClose,
        ),
      );
    }

    final spacedHeaderActions = <Widget>[];
    for (final button in headerActionButtons) {
      if (spacedHeaderActions.isNotEmpty) {
        spacedHeaderActions.add(const SizedBox(width: 6));
      }
      spacedHeaderActions.add(button);
    }

    return Container(
      height: 48,
      padding: const EdgeInsets.symmetric(horizontal: 12),
      color: Theme.of(context).colorScheme.surface,
      child: Row(
        children: [
          if (dragHandle != null) dragHandle,
          // Connection status indicator
          Container(
            width: 10,
            height: 10,
            decoration: BoxDecoration(
              color: model.isConnected ? successGreen : errorRed,
              shape: BoxShape.circle,
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Row(
              children: [
                Expanded(child: _buildTitleWithCwd(model)),
                if (usageButtonHeader != null) ...[
                  const SizedBox(width: 6),
                  usageButtonHeader,
                ],
              ],
            ),
          ),
          if (spacedHeaderActions.isNotEmpty) ...[
            const SizedBox(width: 6),
            ...spacedHeaderActions,
          ],
        ],
      ),
    );
  }

  Future<void> _openFolderPicker(ChatModel model) async {
    final selected = await showDialog<String?>(
      context: context,
      builder: (_) => FolderPickerDialog(client: model.client),
    );
    if (selected != null && selected.isNotEmpty) {
      model.client.setInitialVariables({'workspace_root': selected});
      model.setCwdLabelFromPath(selected);
    }
  }

  Future<void> _openTerminal(ChatModel model) async {
    try {
      await model.ensureTerminal(preferredCwd: model.cwdPath);
    } on StateError catch (err) {
      if (err.message == 'session_required') {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
              content: Text('Start a session before opening the terminal.')),
        );
        return;
      }
      rethrow;
    }
    if (!mounted) return;
    if (widget.dockTerminals) {
      model.setTerminalDocked(true);
      return;
    }
    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => ChangeNotifierProvider.value(
        value: model.terminalDeck,
        child: const TerminalSheet(),
      ),
    );
  }
}

// -----------------------------------------------------------------------------
// Model / state layer ----------------------------------------------------------
// -----------------------------------------------------------------------------

class ChatModel extends ChangeNotifier {
  ChatModel(this._controller, {this.sessionId, this.onRunStarted})
      : terminalDeck = TerminalDeck(_controller.client) {
    terminalDeck.sessionHint = _controller.sessionId ?? sessionId;
    terminalDeck.addListener(_handleTerminalDeckChanged);
    _subscription = _controller.events.listen(_handleEvent);
    _initHistory();
    _loadGlobalHistory();
  }

  final ConversationController _controller;
  RemoteAgentClient get client => _controller.client;
  late final StreamSubscription<RemoteEvent> _subscription;

  final List<ChatItem> _items = [];
  List<ChatItem> get items => List.unmodifiable(_items);

  bool _isStreaming = false;
  bool get isStreaming => _isStreaming;

  bool _isConnected = false;
  bool get isConnected => _isConnected;

  final String? sessionId;
  final void Function(String?, String?)? onRunStarted;

  String? statusText;
  bool statusShowSpinner = false;
  String? currentRunId;
  String? modelName;
  int? contextWindow;
  UsageDetails? usageTotals;
  final List<String> globalHistory = [];
  int historyIndex = 0;
  String historyDraft = '';
  String? cwdBase;
  String? cwdPath;
  late final TerminalDeck terminalDeck;

  void _handleTerminalDeckChanged() {
    notifyListeners();
  }

  Future<void> refreshCwd() async {
    try {
      final res = await client.serverCall('fs_get_workspace_root');
      final p = res['path']?.toString();
      if (p != null && p.isNotEmpty) {
        cwdPath = p;
        cwdBase = _basename(p);
        terminalDeck.defaultCwd = cwdPath;
        notifyListeners();
        return;
      }
    } catch (_) {}
    try {
      final res = await client.serverCall('fs_get_cwd');
      final p = res['path']?.toString();
      if (p != null && p.isNotEmpty) {
        cwdPath = p;
        cwdBase = _basename(p);
        terminalDeck.defaultCwd = cwdPath;
        notifyListeners();
      }
    } catch (_) {}
  }

  void setCwdLabelFromPath(String path) {
    if (path.isEmpty) return;
    cwdPath = path;
    cwdBase = _basename(path);
    terminalDeck.defaultCwd = cwdPath;
    notifyListeners();
  }

  Future<void> ensureTerminal({String? preferredCwd}) async {
    final active = _controller.sessionId ?? sessionId;
    if (active == null || active.isEmpty) {
      throw StateError('session_required');
    }
    if (preferredCwd != null && preferredCwd.isNotEmpty) {
      terminalDeck.defaultCwd = preferredCwd;
    }
    terminalDeck.updateSessionHint(active);
    await terminalDeck.ensureActive(preferredCwd: preferredCwd);
  }

  Future<void> addTerminalTab({String? preferredCwd}) async {
    final active = _controller.sessionId ?? sessionId;
    if (active == null || active.isEmpty) {
      throw StateError('session_required');
    }
    if (preferredCwd != null && preferredCwd.isNotEmpty) {
      terminalDeck.defaultCwd = preferredCwd;
    }
    terminalDeck.updateSessionHint(active);
    await terminalDeck.addSession(preferredCwd: preferredCwd ?? cwdPath);
  }

  void setTerminalDocked(bool value) {
    terminalDeck.setDocked(value);
  }

  bool get terminalDocked => terminalDeck.docked;

  RemoteTerminalSession? get activeTerminalSession =>
      terminalDeck.activeSession;

  void closeDockedTerminal() {
    terminalDeck.setDocked(false);
    terminalDeck.clear();
  }

  String _basename(String input) {
    var p = input.trim();
    while (p.endsWith('/') || p.endsWith('\\')) {
      p = p.substring(0, p.length - 1);
    }
    if (p.isEmpty) return '';
    final sep = p.contains('\\') && !p.contains('/') ? '\\' : '/';
    final parts = p.split(sep).where((s) => s.isNotEmpty).toList();
    return parts.isEmpty ? p : parts.last;
  }

  void send(String text) async {
    debugPrint('[chat] send "$text"');
    if (text.trim().startsWith('/')) {
      await _handleSlashCommand(text.trim());
      return;
    }

    _items.add(UserMessage(text));
    _recordHistory(text);
    _isStreaming = true;
    statusText = null;
    statusShowSpinner = false;
    notifyListeners();

    try {
      final ids = await _controller.startRun(text);
      final sessionForCallback =
          (ids.sessionId != null && ids.sessionId!.isNotEmpty)
              ? ids.sessionId
              : _controller.sessionId;
      onRunStarted?.call(ids.runId, sessionForCallback);
      if (sessionForCallback != null && sessionForCallback.isNotEmpty) {
        terminalDeck.updateSessionHint(sessionForCallback);
      }
      if (sessionForCallback != null && sessionForCallback.isNotEmpty) {
        // ignore: discarded_futures
        refreshCwd();
      }
      if (ids.runId != null && ids.runId!.isNotEmpty) {
        currentRunId = ids.runId;
      }
    } catch (e) {
      _isStreaming = false;
      if (e is RemoteRpcError) {
        statusText ??= 'Error: ${e.message}';
      } else {
        statusText ??= 'Error: $e';
      }
      statusShowSpinner = false;
    }
    notifyListeners();
  }

  void cancelActiveRun() async {
    final runId = currentRunId;
    if (runId == null || runId.isEmpty) return;
    // Optimistic UI update
    currentRunId = null;
    _isStreaming = false;
    // Mark any running tool calls as cancelled
    for (final i in _items) {
      if (i is ToolInteractionCard && i.output == null && i.running) {
        i.output = '[Cancelled]';
        i.running = false;
      }
    }
    _items.add(AssistantMessage('[Run cancelled by user]'));
    statusText = null;
    statusShowSpinner = false;
    notifyListeners();
    try {
      await _controller.stopRun(runId);
    } catch (_) {}
  }

  Future<void> _handleSlashCommand(String cmd) async {
    final parts = cmd.split(' ');
    final name = parts.isNotEmpty ? parts.first.toLowerCase() : '';
    final arg =
        cmd.length > name.length ? cmd.substring(name.length).trimLeft() : '';

    if (name == '/help') {
      try {
        final funcs = await client.listServerFunctions();
        final buffer = StringBuffer();
        buffer.writeln('Available commands:');
        buffer.writeln();
        buffer.writeln('- /help – Show this list');
        for (final f in funcs) {
          final fname = f['name']?.toString() ?? '';
          if (fname.isEmpty) continue;
          final desc = f['description']?.toString();
          String argHint = '';
          final schema = f['params_schema'];
          if (schema is Map && schema['properties'] is Map) {
            final props = (schema['properties'] as Map).cast<String, dynamic>();
            if (props.containsKey('text')) argHint = ' <text>';
          }
          final d = (desc == null || desc.isEmpty)
              ? "Invoke server function '$fname'"
              : desc;
          buffer.writeln('- /$fname$argHint – $d');
        }
        _items.add(AssistantMessage(buffer.toString().trimRight()));
      } catch (e) {
        _items.add(AssistantMessage('Failed to fetch commands: $e'));
      }
      notifyListeners();
      return;
    }

    if (name.startsWith('/')) {
      final fn = name.substring(1);
      try {
        final funcs = await client.listServerFunctions();
        bool found = false;
        for (final f in funcs) {
          final fname = f['name']?.toString();
          if (fname != null && fname.toLowerCase() == fn.toLowerCase()) {
            found = true;
            break;
          }
        }
        if (!found) {
          // Fallback to LLM: treat as normal user prompt
          _items.add(UserMessage(cmd));
          _recordHistory(cmd);
          notifyListeners();
          try {
            final ids = await _controller.startRun(cmd);
            final sessionForCallback =
                (ids.sessionId != null && ids.sessionId!.isNotEmpty)
                    ? ids.sessionId
                    : _controller.sessionId;
            onRunStarted?.call(ids.runId, sessionForCallback);
            _isStreaming = true;
            statusText = null;
            statusShowSpinner = false;
          } catch (_) {}
          notifyListeners();
          return;
        }

        Map<String, dynamic> argsMap = {};
        if (arg.isNotEmpty) {
          try {
            final any = jsonDecode(arg);
            if (any is Map<String, dynamic>) {
              argsMap = any;
            } else if (any is List) {
              argsMap = {'args': any};
            } else if (any is String) {
              argsMap = {'text': any};
            } else {
              argsMap = {'value': any};
            }
          } catch (_) {
            argsMap = {'text': arg};
          }
        }

        final out = await client.serverCall(fn, args: argsMap);
        final msg = out['message'];
        if (msg is String && msg.trim().isNotEmpty) {
          _items.add(AssistantMessage(msg));
        } else {
          String pretty;
          try {
            pretty = const JsonEncoder.withIndent('  ').convert(out);
          } catch (_) {
            pretty = out.toString();
          }
          if (pretty.trim().isEmpty || pretty == 'null') pretty = 'Done.';
          _items.add(AssistantMessage(pretty));
        }
        notifyListeners();
        return;
      } catch (e) {
        _items.add(AssistantMessage('Error: $e'));
        notifyListeners();
        return;
      }
    }
  }

  void _recordHistory(String text) {
    final t = text.trim();
    if (t.isEmpty) return;
    if (globalHistory.isEmpty || globalHistory.last != t) {
      // dedupe simple
      if (globalHistory.isEmpty || !identical(globalHistory.last, t)) {
        globalHistory.add(t);
      }
    }
    historyIndex = globalHistory.length;
  }

  String? historyUp(String current) {
    if (globalHistory.isEmpty) return null;
    if (historyIndex > globalHistory.length)
      historyIndex = globalHistory.length;
    if (historyIndex == globalHistory.length) {
      historyDraft = current;
      if (historyIndex > 0) historyIndex = historyIndex - 1;
    } else if (historyIndex > 0) {
      historyIndex = historyIndex - 1;
    }
    if (historyIndex >= 0 && historyIndex < globalHistory.length) {
      return globalHistory[historyIndex];
    }
    return null;
  }

  String? historyDown() {
    if (globalHistory.isEmpty) return null;
    if (historyIndex < globalHistory.length - 1) {
      historyIndex = historyIndex + 1;
      return globalHistory[historyIndex];
    }
    if (historyIndex == globalHistory.length - 1) {
      historyIndex = globalHistory.length;
      return historyDraft;
    }
    return null;
  }

  Future<void> _loadGlobalHistory() async {
    try {
      final items =
          await client.getUserHistory(limit: 200, includeArchived: true);
      final seen = <String>{};
      for (final t in items) {
        final s = t.trim();
        if (s.isEmpty) continue;
        if (seen.add(s)) globalHistory.add(s);
      }
      historyIndex = globalHistory.length;
    } catch (_) {}
  }

  void _handleEvent(RemoteEvent event) {
    if (event is MessageOutputEvent) {
      debugPrint('[chat ${client.label ?? '?'}] message run=${event.runId} '
          'final=${event.isFinal} chunk=${event.content}');

      if (_items.isNotEmpty && _items.last is AssistantMessage) {
        // Append to last assistant message
        final last = _items.last as AssistantMessage;
        last.text += event.content;
      } else {
        _items.add(AssistantMessage(event.content));
      }

      if (_isStreaming) {
        statusText = null;
        statusShowSpinner = false;
      }
      if (event.isFinal) {
        _isStreaming = false;
      }
    } else if (event is ToolCalledEvent) {
      debugPrint('[chat] tool called ${event.tool} ${event.input}');
      _items.add(ToolInteractionCard(
          event.callId, event.tool, event.input, null,
          metadata: null, running: true));
      statusText = null;
      statusShowSpinner = false;
    } else if (event is ToolResultEvent) {
      debugPrint('[chat] tool result ${event.tool}');
      // Try to find the interaction card with matching callId
      ToolInteractionCard? interaction;
      if (event.callId != null) {
        for (final i in _items) {
          if (i is ToolInteractionCard && i.callId == event.callId) {
            interaction = i;
            break;
          }
        }
      }

      // Fallback: find last interaction without result
      if (interaction == null) {
        for (var j = _items.length - 1; j >= 0; j--) {
          final i = _items[j];
          if (i is ToolInteractionCard && i.output == null) {
            interaction = i;
            break;
          }
        }
      }

      if (interaction != null) {
        interaction.output = event.output;
        interaction.metadata = event.metadata;
        interaction.running = false;
        // If tool name placeholder, overwrite
        if (interaction.tool.isEmpty || interaction.tool == '<tool>') {
          interaction.tool = event.tool;
        }
      } else {
        // Edge-case: no matching call card – create one directly.
        _items.add(ToolInteractionCard(
            event.callId, event.tool, '', event.output,
            metadata: event.metadata, running: false));
      }
      statusText = null;
      statusShowSpinner = false;
    } else if (event is ErrorEvent) {
      debugPrint('[chat] error ${event.message}');
      statusText = 'Error: ${event.message}';
      statusShowSpinner = false;
      _isStreaming = false;
    } else if (event is RunEndEvent) {
      if (currentRunId == null || event.runId == currentRunId) {
        _isStreaming = false;
        if ((event.reason.toLowerCase() == 'error') && event.error != null) {
          statusText = 'Error: ${event.error!.message}';
          statusShowSpinner = false;
        } else {
          statusText = null;
          statusShowSpinner = false;
        }
        if (event.model != null) modelName = event.model;
        if (event.contextWindow != null) contextWindow = event.contextWindow;
        if (event.usage != null) usageTotals = event.usage;
        currentRunId = null;
      }
    } else if (event is ToolApprovalRequestEvent) {
      final requestId = event.requestId;
      _items.add(ApprovalRequestCard(
          event.approvalId, event.tool, event.arguments, requestId,
          (bool approved, bool always) {
        final result = <String, dynamic>{
          'approved': approved,
          'always_approve': always,
        };
        if (!approved) {
          result['error_message'] = 'User rejected tool call';
        }
        client.clientResponse(requestId, ok: true, result: result);

        _items.removeWhere(
            (i) => i is ApprovalRequestCard && i.requestId == requestId);
        notifyListeners();
      }, metadata: event.metadata));
    } else if (event is ConnectedEvent) {
      _isConnected = true;
      if (statusText != null &&
          statusText!.toLowerCase().contains('disconnected')) {
        statusText = null;
        statusShowSpinner = false;
      }
    } else if (event is DisconnectedEvent) {
      _isConnected = false;
      _isStreaming = false;
      statusText = 'Disconnected from server';
      statusShowSpinner = false;
      terminalDeck.markDisconnected();
    } else if (event is RunStatusEvent) {
      if (currentRunId == null || event.runId == currentRunId) {
        final a = event.attempt;
        final t = event.total;
        final msg = event.message;
        statusText = t > 0 && a > 0 ? 'Retrying $a/$t: $msg' : 'Retrying: $msg';
        statusShowSpinner = true;
      }
    } else if (event is RunStartedEvent) {
      currentRunId = event.runId;
      if (event.sessionId.isNotEmpty) {
        terminalDeck.updateSessionHint(event.sessionId);
        refreshCwd();
      }
      statusText = null;
      statusShowSpinner = false;
    } else if (event is TokenUsageEvent) {
      usageTotals = event.totals;
      if (event.model != null) modelName = event.model;
      if (event.contextWindow != null) contextWindow = event.contextWindow;
    } else if (event is ClientRequestEvent) {
      final fn = event.function.toLowerCase();
      if (fn == 'ui.set_status') {
        final text = event.args['text']?.toString() ?? '';
        final show = event.args['show_spinner'];
        bool showSpin = true;
        if (show is bool) showSpin = show;
        statusText = text;
        statusShowSpinner = showSpin;
        notifyListeners();
        client.clientResponse(event.requestId, ok: true, result: {'ack': true});
        return;
      } else {
        client.clientResponse(event.requestId, ok: false, error: {
          'message': 'Unsupported client function: ${event.function}',
          'type': 'UnsupportedFunction'
        });
        return;
      }
    }
    notifyListeners();
  }

  Future<void> _initHistory() async {
    if (sessionId == null) return;
    try {
      final hist = await client.getSessionHistory(sessionId!);
      debugPrint('[chat] loaded history entries: ${hist.length}');
      client.resumeSession(sessionId!);
      notifyListeners();
      // Refresh cwd label for resumed sessions
      // ignore: discarded_futures
      refreshCwd();
      // Keep a lookup so that we can merge function call results into the
      // corresponding call card – this mirrors the behaviour in
      // [_handleEvent] for live streams.
      final Map<String?, ToolInteractionCard> interactionByCallId = {};

      for (final raw in hist) {
        if (raw is Map<String, dynamic>) {
          final role = raw['role'] as String?;
          final type = raw['type'] as String?;

          if (role == 'user' || type == 'user_message') {
            _items.add(UserMessage(raw['content']?.toString() ?? ''));
          } else if (role == 'assistant' ||
              type == 'assistant_message' ||
              type == 'message') {
            // Content may be a plain string or a list of segments (e.g. OpenAI chat format).
            String contentText = '';
            final rawContent = raw['content'];
            if (rawContent is String) {
              contentText = rawContent;
            } else if (rawContent is List) {
              // Concatenate all string or map segments we can extract.
              final buffer = StringBuffer();
              for (final part in rawContent) {
                if (part is String) {
                  buffer.write(part);
                } else if (part is Map) {
                  if (part['text'] is String) {
                    buffer.write(part['text']);
                  } else if (part['content'] is String) {
                    buffer.write(part['content']);
                  } else if (part['value'] is String) {
                    buffer.write(part['value']);
                  }
                }
              }
              contentText = buffer.toString();
              if (contentText.isEmpty) {
                // Fallback – show raw content for debugging.
                contentText = rawContent.toString();
              }
            } else if (rawContent is Map) {
              // Try common keys.
              if (rawContent['text'] is String) {
                contentText = rawContent['text'] as String;
              } else if (rawContent['content'] is String) {
                contentText = rawContent['content'] as String;
              } else if (rawContent['value'] is String) {
                contentText = rawContent['value'] as String;
              } else {
                contentText = rawContent.toString();
              }
            }
            _items.add(AssistantMessage(contentText));
          } else if (type == 'function_call') {
            final callId = (raw['call_id'] ?? raw['id'])?.toString();
            final name =
                raw['name'] as String? ?? raw['tool']?.toString() ?? '<tool>';
            final args =
                raw['arguments']?.toString() ?? raw['input']?.toString() ?? '';

            final interaction = ToolInteractionCard(callId, name, args, null);
            _items.add(interaction);
            if (callId != null) interactionByCallId[callId] = interaction;
          } else if (type == 'function_call_output') {
            final callId = (raw['call_id'] ?? raw['id'])?.toString();
            final name =
                raw['name'] as String? ?? raw['tool']?.toString() ?? '<tool>';
            final output = raw['output']?.toString() ?? '';
            final meta = (raw['metadata'] as Map?)?.cast<String, dynamic>();

            ToolInteractionCard? interaction;
            if (callId != null && interactionByCallId.containsKey(callId)) {
              interaction = interactionByCallId[callId];
            }

            // Fallback: merge with last interaction without result.
            if (interaction == null) {
              for (var j = _items.length - 1; j >= 0; j--) {
                final item = _items[j];
                if (item is ToolInteractionCard && item.output == null) {
                  interaction = item;
                  break;
                }
              }
            }

            if (interaction != null) {
              interaction.output = output;
              if (meta != null) interaction.metadata = meta;
              if (interaction.tool.isEmpty || interaction.tool == '<tool>') {
                interaction.tool = name;
              }
            } else {
              // No matching call card – create a standalone interaction.
              _items.add(ToolInteractionCard(callId, name, '', output,
                  metadata: meta, running: false));
            }
          }
        }
      }
      if (_items.isNotEmpty) notifyListeners();
    } catch (_) {
      // Ignore failures (e.g., session not found)
    }
  }

  @override
  void dispose() {
    _subscription.cancel();
    terminalDeck.removeListener(_handleTerminalDeckChanged);
    terminalDeck.dispose();
    super.dispose();
  }
}

// -----------------------------------------------------------------------------
// UI item helper classes -------------------------------------------------------
// -----------------------------------------------------------------------------

abstract class ChatItem {}

class UserMessage implements ChatItem {
  UserMessage(this.text);
  final String text;
}

class AssistantMessage implements ChatItem {
  AssistantMessage(this.text);
  String text;
}

class ToolCallCard implements ChatItem {
  ToolCallCard(this.tool, this.arguments);
  final String tool;
  final String arguments;
}

class ToolResultCard implements ChatItem {
  ToolResultCard(this.tool, this.output);
  final String tool;
  final String output;
}

class ApprovalRequestCard implements ChatItem {
  ApprovalRequestCard(this.approvalId, this.tool, this.arguments,
      this.requestId, this.onDecision,
      {this.metadata});
  final String approvalId;
  final String tool;
  final String arguments;
  final String requestId;
  final Function(bool, bool) onDecision;
  final Map<String, dynamic>? metadata;
}

class ErrorBanner implements ChatItem {
  ErrorBanner(this.message);
  final String message;
}

class ToolInteractionCard implements ChatItem {
  ToolInteractionCard(this.callId, this.tool, this.input, this.output,
      {this.metadata, this.running = false});
  final String? callId;
  String tool;
  String input;
  String? output;
  Map<String, dynamic>? metadata;
  bool running;
}

// ---------------------------------------------------------------------------
// Top-level collapsible card widget -----------------------------------------
// ---------------------------------------------------------------------------

class _InnerCollapsibleCard extends StatefulWidget {
  const _InnerCollapsibleCard({
    required this.iconColor,
    required this.icon,
    required this.title,
    this.body,
    this.bodyWidget,
    this.initiallyExpanded = false,
    this.showIcon = true,
  });

  final Color iconColor;
  final IconData icon;
  final String title;
  final String? body;
  final Widget? bodyWidget;
  final bool initiallyExpanded;
  final bool showIcon;

  @override
  State<_InnerCollapsibleCard> createState() => _InnerCollapsibleCardState();
}

class _InnerCollapsibleCardState extends State<_InnerCollapsibleCard> {
  late bool _expanded;

  @override
  void initState() {
    super.initState();
    _expanded = widget.initiallyExpanded;
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
      child: GestureDetector(
        onTap: (widget.body == null && widget.bodyWidget == null)
            ? null
            : () {
                setState(() {
                  _expanded = !_expanded;
                });
              },
        child: Container(
          decoration: BoxDecoration(
            color: scheme.surfaceVariant,
            borderRadius: BorderRadius.circular(8),
            border: Border.all(color: widget.iconColor.withOpacity(0.12)),
          ),
          padding: const EdgeInsets.all(12),
          child: Row(
            crossAxisAlignment:
                _expanded || (widget.body == null && widget.bodyWidget == null)
                    ? CrossAxisAlignment.start
                    : CrossAxisAlignment.center,
            children: [
              if (widget.showIcon) ...[
                Container(
                  width: 20,
                  height: 20,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: Colors.transparent,
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(
                      color: widget.iconColor.withOpacity(0.8),
                      width: 1.2,
                    ),
                  ),
                  child: Icon(
                    widget.icon,
                    size: 12,
                    color: widget.iconColor.withOpacity(0.9),
                  ),
                ),
                const SizedBox(width: 8),
              ],
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(widget.title,
                              style: const TextStyle(
                                  fontWeight: FontWeight.w500, fontSize: 13)),
                        ),
                        if (widget.body != null || widget.bodyWidget != null)
                          Icon(
                            _expanded ? Icons.expand_less : Icons.expand_more,
                            size: 16,
                            color: scheme.onSurfaceVariant,
                          ),
                      ],
                    ),
                    if (_expanded &&
                        (widget.bodyWidget != null || widget.body != null)) ...[
                      const SizedBox(height: 6),
                      if (widget.bodyWidget != null)
                        widget.bodyWidget!
                      else
                        Text(widget.body!,
                            style: const TextStyle(fontSize: 12)),
                    ]
                  ],
                ),
              )
            ],
          ),
        ),
      ),
    );
  }
}
