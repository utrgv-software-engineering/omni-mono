import 'package:flutter/material.dart';
import 'dart:async';
import 'package:provider/provider.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'dart:math' as math;
import 'dart:convert';

import 'models.dart';
import 'remote_agent_client.dart';
import 'theme/app_colors.dart';

/// Simple chat screen that talks to a remote OmniAgents server.
class ChatScreen extends StatelessWidget {
  const ChatScreen({
    required this.client,
    required this.title,
    this.sessionId,
    this.onClose,
    this.onRunStarted,
    this.embedded = false,
    this.model,
    this.dragIndex,
    super.key,
  });

  final RemoteAgentClient client;
  final String title;
  final String? sessionId;
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
        ),
      );
    }

    return ChangeNotifierProvider(
      create: (_) =>
          ChatModel(client, sessionId: sessionId, onRunStarted: onRunStarted),
      child: _ChatView(
        title: title,
        onClose: onClose,
        embedded: embedded,
        dragIndex: dragIndex,
      ),
    );
  }
}

class _ChatView extends StatefulWidget {
  const _ChatView(
      {required this.title,
      this.onClose,
      this.embedded = false,
      this.dragIndex});

  final String title;
  final VoidCallback? onClose;
  final bool embedded;
  final int? dragIndex;

  @override
  State<_ChatView> createState() => _ChatViewState();
}

class _ChatViewState extends State<_ChatView> {
  final _scrollController = ScrollController();
  final _inputController = TextEditingController();

  // Tracks the number of chat items we have already rendered so that we only
  // scroll to the bottom when new items arrive, not on every rebuild (e.g.
  // when the user simply taps inside the column).
  int _lastItemCount = 0;

  @override
  void dispose() {
    _scrollController.dispose();
    _inputController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final model = context.watch<ChatModel>();

    WidgetsBinding.instance.addPostFrameCallback((_) {
      // Only auto-scroll if new content has been appended to the list since
      // the previous frame. This prevents unwanted jumps when the user
      // interacts with the existing UI (for example tapping on a column).
      if (model.items.length != _lastItemCount &&
          _scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
        );
        _lastItemCount = model.items.length;
      }
    });

    // Wrap the entire chat column in a SafeArea so the bottom system insets
    // (e.g. iOS home-indicator) never obscure the input row.  We disable the
    // other insets because the header/app-bar already handles the top and the
    // horizontal padding is managed elsewhere.
    final content = SafeArea(
      top: false,
      left: false,
      right: false,
      child: Column(
        children: [
          // Header (only for embedded mode)
          if (widget.embedded) _buildHeader(model),
          Expanded(
            child: ListView.builder(
              controller: _scrollController,
              itemCount: model.items.length,
              itemBuilder: (context, index) {
                final item = model.items[index];
                return _buildItem(item);
              },
            ),
          ),
          if (model.isStreaming)
            const Padding(
              padding: EdgeInsets.all(8.0),
              child: Text('Assistant is typing…'),
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
        title: Text(widget.title),
        bottom: const PreferredSize(
          preferredSize: Size.fromHeight(1),
          child: Divider(height: 1, thickness: 1, color: bgCardAlt),
        ),
        actions: widget.onClose == null
            ? null
            : [
                IconButton(
                  icon: const Icon(Icons.close),
                  tooltip: 'Close',
                  onPressed: widget.onClose,
                )
              ],
      ),
      body: content,
    );
  }

  Widget _buildInputRow(ChatModel model) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 16),
      child: TextField(
        controller: _inputController,
        minLines: 1,
        maxLines: 5,
        textInputAction: TextInputAction.send,
        onSubmitted: (_) => _send(model),
        decoration: InputDecoration(
          hintText: 'Type a message…',
          // Use filled style to give the input its own subtle surface.
          filled: true,
          fillColor: Theme.of(context).colorScheme.surfaceVariant,
          // Remove the decoration border and round the corners.
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(24),
            borderSide: BorderSide.none,
          ),
          contentPadding:
              const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          suffixIcon: IconButton(
            icon: const Icon(Icons.send),
            onPressed: model.isStreaming ? null : () => _send(model),
          ),
        ),
      ),
    );
  }

  void _send(ChatModel model) {
    final text = _inputController.text.trim();
    if (text.isEmpty) return;
    _inputController.clear();
    debugPrint('[chat] send "$text"');
    model.send(text);
  }

  Widget _buildItem(ChatItem item) {
    final scheme = Theme.of(context).colorScheme;
    if (item is UserMessage) {
      return _bubble(item.text, isUser: true, bubbleColor: scheme.primary);
    } else if (item is AssistantMessage) {
      return _bubble(item.text,
          isUser: false, bubbleColor: scheme.surfaceVariant, useMarkdown: true);
    } else if (item is ToolCallCard) {
      return _toolCallCard(item.tool, item.arguments);
    } else if (item is ToolResultCard) {
      return _toolResultCard(item.tool, item.output);
    } else if (item is ApprovalRequestCard) {
      return _approvalRequestCard(context, item);
    } else if (item is ErrorBanner) {
      return ListTile(
        tileColor: errorRed.withOpacity(0.15),
        title: Text(item.message),
      );
    } else if (item is ToolInteractionCard) {
      return _toolInteractionCard(item.tool, item.input, item.output);
    }
    return const SizedBox.shrink();
  }

  // Helpers -----------------------------------------------------------------

  Widget _bubble(String text,
      {required bool isUser,
      required Color bubbleColor,
      bool useMarkdown = false}) {
    final onColor =
        ThemeData.estimateBrightnessForColor(bubbleColor) == Brightness.dark
            ? Colors.white
            : Colors.black87;

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
          decoration: BoxDecoration(
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
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
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
    );
  }

  Widget _approvalRequestCard(BuildContext context, ApprovalRequestCard item) {
    // Use the same visual language as the other tool cards for consistency.
    const iconColor = warningOrange;
    final scheme = Theme.of(context).colorScheme;

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
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

  Widget _toolInteractionCard(String tool, String input, String? output) {
    final hasResult = output != null && output.isNotEmpty;
    final iconColor =
        hasResult ? successGreen : Theme.of(context).colorScheme.primary;
    final icon = hasResult ? Icons.check : Icons.build;

    // Build a richer body with JSON aware formatting.
    final scheme = Theme.of(context).colorScheme;
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

    if (input.isNotEmpty) {
      addSection('input', input, scheme.primary);
    }

    if (hasResult) {
      addSection('output', output, successGreen);
    }

    if (children.isNotEmpty) {
      // Remove last spacer
      if (children.last is SizedBox) children.removeLast();
      return _CollapsibleCard(
        iconColor: iconColor,
        icon: icon,
        title: 'Tool $tool',
        body: null,
        bodyWidget: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: children,
        ),
        initiallyExpanded: false,
      );
    }
    return const SizedBox.shrink();
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
  }) {
    return _InnerCollapsibleCard(
      iconColor: iconColor,
      icon: icon,
      title: title,
      body: body,
      bodyWidget: bodyWidget,
      initiallyExpanded: initiallyExpanded,
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
            child: Text(
              widget.title,
              style: const TextStyle(
                fontWeight: FontWeight.bold,
                fontSize: 14,
              ),
            ),
          ),
          if (widget.onClose != null)
            IconButton(
              padding: EdgeInsets.zero,
              iconSize: 20,
              icon: const Icon(Icons.close),
              onPressed: widget.onClose,
            ),
        ],
      ),
    );
  }
}

// -----------------------------------------------------------------------------
// Model / state layer ----------------------------------------------------------
// -----------------------------------------------------------------------------

class ChatModel extends ChangeNotifier {
  ChatModel(this._client, {this.sessionId, this.onRunStarted}) {
    _subscription = _client.events.listen(_handleEvent);
    _initHistory();
  }

  final RemoteAgentClient _client;
  late final StreamSubscription<RemoteEvent> _subscription;

  final List<ChatItem> _items = [];
  List<ChatItem> get items => List.unmodifiable(_items);

  bool _isStreaming = false;
  bool get isStreaming => _isStreaming;

  bool _isConnected = false;
  bool get isConnected => _isConnected;

  final String? sessionId;
  final void Function(String?, String?)? onRunStarted;

  void send(String text) async {
    debugPrint('[chat] send "$text"');
    _items.add(UserMessage(text));
    notifyListeners();

    try {
      final ids = await _client.startRun(text);
      onRunStarted?.call(ids.runId, ids.sessionId);
      _isStreaming = true;
    } catch (e) {
      // Connection failed: state is already updated via ErrorEvent/DisconnectedEvent.
    }
    notifyListeners();
  }

  void _handleEvent(RemoteEvent event) {
    if (event is MessageOutputEvent) {
      debugPrint(
          '[chat] message chunk ${event.content} final=${event.isFinal}');

      if (_items.isNotEmpty && _items.last is AssistantMessage) {
        // Append to last assistant message
        final last = _items.last as AssistantMessage;
        last.text += event.content;
      } else {
        _items.add(AssistantMessage(event.content));
      }

      if (event.isFinal) {
        _isStreaming = false;
      }
    } else if (event is ToolCalledEvent) {
      debugPrint('[chat] tool called ${event.tool} ${event.input}');
      _items.add(
          ToolInteractionCard(event.callId, event.tool, event.input, null));
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
        // If tool name placeholder, overwrite
        if (interaction.tool.isEmpty || interaction.tool == '<tool>') {
          interaction.tool = event.tool;
        }
      } else {
        // Edge-case: no matching call card – create one directly.
        _items.add(
            ToolInteractionCard(event.callId, event.tool, '', event.output));
      }
    } else if (event is ErrorEvent) {
      debugPrint('[chat] error ${event.message}');
      _items.add(ErrorBanner(event.message));
      _isStreaming = false;
    } else if (event is RunEndEvent) {
      _isStreaming = false;
    } else if (event is ToolApprovalRequestEvent) {
      _items.add(
          ApprovalRequestCard(event.approvalId, event.tool, event.arguments,
              (bool approved, bool always) {
        // First inform the backend about the user decision…
        _client.toolApprovalResponse(
            approvalId: event.approvalId,
            approved: approved,
            alwaysApprove: always);

        // Then remove the card from the list so the UI no longer shows the
        // approval banner.
        _items.removeWhere((i) =>
            i is ApprovalRequestCard &&
            i.approvalId == event.approvalId &&
            i.tool == event.tool);
        notifyListeners();
      }));
    } else if (event is ConnectedEvent) {
      _isConnected = true;
    } else if (event is DisconnectedEvent) {
      _isConnected = false;
      _isStreaming = false;
    }
    notifyListeners();
  }

  Future<void> _initHistory() async {
    if (sessionId == null) return;
    try {
      final hist = await _client.getSessionHistory(sessionId!);
      debugPrint('[chat] loaded history entries: ${hist.length}');
      _client.resumeSession(sessionId!);
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
              if (interaction.tool.isEmpty || interaction.tool == '<tool>') {
                interaction.tool = name;
              }
            } else {
              // No matching call card – create a standalone interaction.
              _items.add(ToolInteractionCard(callId, name, '', output));
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
  ApprovalRequestCard(
      this.approvalId, this.tool, this.arguments, this.onDecision);
  final String approvalId;
  final String tool;
  final String arguments;
  final Function(bool, bool) onDecision;
}

class ErrorBanner implements ChatItem {
  ErrorBanner(this.message);
  final String message;
}

class ToolInteractionCard implements ChatItem {
  ToolInteractionCard(this.callId, this.tool, this.input, this.output);
  final String? callId;
  String tool;
  String input;
  String? output;
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
  });

  final Color iconColor;
  final IconData icon;
  final String title;
  final String? body;
  final Widget? bodyWidget;
  final bool initiallyExpanded;

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
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
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
              Container(
                width: 20,
                height: 20,
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  // Transparent fill for low-profile appearance.
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
