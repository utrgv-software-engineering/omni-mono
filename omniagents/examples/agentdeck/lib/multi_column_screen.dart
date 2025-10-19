import 'dart:convert';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'chat_screen.dart';
import 'remote_agent_client.dart';
import 'conversation_controller.dart';
import 'agents_repository.dart';
import 'theme/app_colors.dart';
import 'terminal_panel.dart';
import 'terminal_session.dart';

enum DeckLayoutMode { deck, focus }

class MultiColumnScreen extends StatefulWidget {
  const MultiColumnScreen({super.key});

  @override
  State<MultiColumnScreen> createState() => _MultiColumnScreenState();
}

class _Conversation {
  _Conversation({required this.id, required this.agent, this.sessionId}) {
    controller = _buildController(agent, sessionId: sessionId);
    _controllerListener = () {
      final resolved = controller.sessionId;
      if (resolved != null && resolved != sessionId) {
        sessionId = resolved;
        model?.terminalDeck.updateSessionHint(resolved);
      }
    };
    controller.addListener(_controllerListener!);
  }

  final String id;
  AgentConfig agent;
  String? sessionId;
  late ConversationController controller;
  ChatModel? model;
  VoidCallback? _controllerListener;
  VoidCallback? _modelListener;
  String? _cachedCwdLabel;
  bool _cachedTerminalDocked = false;
  RemoteTerminalSession? _cachedTerminalSession;
  int _cachedTerminalCount = 0;

  RemoteAgentClient get client => controller.client;

  void resetAgent(AgentConfig newAgent) {
    if (_modelListener != null) {
      model?.removeListener(_modelListener!);
      _modelListener = null;
    }
    model?.dispose();
    model = null;
    _cachedCwdLabel = null;
    _cachedTerminalDocked = false;
    _cachedTerminalSession = null;
    _cachedTerminalCount = 0;
    if (_controllerListener != null) {
      controller.removeListener(_controllerListener!);
    }
    controller.dispose();
    agent = newAgent;
    controller = _buildController(newAgent, sessionId: sessionId);
    _controllerListener = () {
      final resolved = controller.sessionId;
      if (resolved != null && resolved != sessionId) {
        sessionId = resolved;
        model?.terminalDeck.updateSessionHint(resolved);
      }
    };
    controller.addListener(_controllerListener!);
  }

  ConversationController _buildController(AgentConfig cfg,
      {String? sessionId}) {
    final remote = RemoteAgentClient(
      cfg.endpoint,
      token: cfg.token,
      debug: true,
      label: id,
    );
    return ConversationController(remote, sessionId: sessionId);
  }

  void dispose() {
    if (_modelListener != null) {
      model?.removeListener(_modelListener!);
      _modelListener = null;
    }
    model?.dispose();
    model = null;
    _cachedCwdLabel = null;
    _cachedTerminalDocked = false;
    _cachedTerminalSession = null;
    _cachedTerminalCount = 0;
    if (_controllerListener != null) {
      controller.removeListener(_controllerListener!);
    }
    controller.dispose();
  }
}

class _MultiColumnScreenState extends State<MultiColumnScreen>
    with WidgetsBindingObserver {
  final List<_Conversation> _conversations = [];
  int _idCounter = 0;

  final ScrollController _hController = ScrollController();
  DeckLayoutMode _layoutMode = DeckLayoutMode.deck;
  String? _selectedConversationId;
  bool _archivingAll = false;
  bool _suppressRestores = false;
  int _loadGeneration = 0;
  double _terminalPaneFraction = 0.35;
  static const double _terminalFractionMin = 0.2;
  static const double _terminalFractionMax = 0.8;
  static const double _terminalDragWidth = 12.0;
  static const double _sidebarWidth = 280.0;

  @override
  void initState() {
    super.initState();
    // Observe app lifecycle changes so we can reconnect when returning to
    // the foreground.
    WidgetsBinding.instance.addObserver(this);
    _loadOrder();
    _loadLayoutMode();
  }

  void _startConversation(AgentConfig agent) {
    setState(() {
      final conv = _Conversation(id: 'c${_idCounter++}', agent: agent);
      _conversations.add(conv);
      _selectedConversationId = conv.id;
    });
    _saveOrder();
  }

  void _closeConversation(String id, {bool archive = true}) {
    setState(() {
      final conv = _conversations.firstWhere((c) => c.id == id);
      if (archive && conv.sessionId != null) {
        // Archive the session on the server so it no longer appears in lists.
        conv.client.archiveSession(conv.sessionId!).catchError((_) {});
      }
      conv.dispose();
      final idx = _conversations.indexOf(conv);
      _conversations.remove(conv);
      if (_selectedConversationId == id) {
        if (_conversations.isEmpty) {
          _selectedConversationId = null;
        } else {
          final nextIndex = idx.clamp(0, _conversations.length - 1);
          _selectedConversationId = _conversations[nextIndex].id;
        }
      }
    });
    _saveOrder();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _hController.dispose();
    // Clients will be disposed by their respective ChatModels.
    super.dispose();
  }

  // -------------------------------------------------------------------------
  // UI ----------------------------------------------------------------------
  // -------------------------------------------------------------------------
  @override
  Widget build(BuildContext context) {
    final repo = context.watch<AgentsRepository>();

    return Scaffold(
      appBar: AppBar(
        title: const Text('AgentDeck'),
        bottom: const PreferredSize(
          preferredSize: Size.fromHeight(1),
          child: Divider(height: 1, thickness: 1, color: bgCardAlt),
        ),
        actions: [
          IconButton(
            tooltip: 'Manage Agents',
            icon: const Icon(Icons.settings),
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute(
                  builder: (_) => _ManageAgentsScreen(
                        onAgentUpdated: _handleAgentUpdated,
                      )),
            ),
          ),
          IconButton(
            tooltip: _archivingAll ? 'Archiving…' : 'Archive All Sessions',
            icon: const Icon(Icons.archive_outlined),
            onPressed: (_archivingAll || !_hasArchivableConversations)
                ? null
                : () => _archiveAllConversations(),
          ),
          IconButton(
            tooltip: _layoutMode == DeckLayoutMode.deck
                ? 'Switch to Focus Layout'
                : 'Switch to Deck Layout',
            icon: Icon(_layoutMode == DeckLayoutMode.deck
                ? Icons.view_sidebar
                : Icons.view_week),
            onPressed: _toggleLayoutMode,
          ),
          IconButton(
            tooltip: 'New Conversation',
            icon: const Icon(Icons.add),
            onPressed: repo.agents.isEmpty ? null : () => _pickAgent(repo),
          ),
        ],
      ),
      body: LayoutBuilder(
        builder: (context, constraints) {
          final width = constraints.maxWidth;
          final hasConversations = _conversations.isNotEmpty;
          if (!hasConversations) {
            return _emptyState();
          }
          if (width < 600) {
            return _buildPagedView();
          }
          if (_layoutMode == DeckLayoutMode.focus && width >= 900) {
            return _buildFocusLayout(repo, width);
          }
          return _buildStrip();
        },
      ),
    );
  }

  // ---------------------------
  // Layout helpers ------------
  // ---------------------------

  ChatModel _ensureModel(_Conversation conv) {
    if (conv.model == null) {
      conv.model = ChatModel(conv.controller, sessionId: conv.sessionId,
          onRunStarted: (runId, sessionId) {
        final controllerSession = conv.controller.sessionId;
        if (controllerSession != null && controllerSession.isNotEmpty) {
          if (conv.sessionId != controllerSession) {
            conv.sessionId = controllerSession;
            _saveOrder();
          }
        }
      });
      conv._cachedCwdLabel = conv.model!.cwdBase;
      conv._cachedTerminalDocked = conv.model!.terminalDocked;
      conv._cachedTerminalSession = conv.model!.activeTerminalSession;
      conv._cachedTerminalCount = conv.model!.terminalDeck.length;
      conv._modelListener = () {
        final model = conv.model;
        if (model == null) return;
        bool changed = false;
        final cwd = model.cwdBase;
        if (cwd != conv._cachedCwdLabel) {
          conv._cachedCwdLabel = cwd;
          changed = true;
        }
        final docked = model.terminalDocked;
        if (docked != conv._cachedTerminalDocked) {
          conv._cachedTerminalDocked = docked;
          changed = true;
        }
        final session = model.activeTerminalSession;
        if (!identical(session, conv._cachedTerminalSession)) {
          conv._cachedTerminalSession = session;
          changed = true;
        }
        final count = model.terminalDeck.length;
        if (count != conv._cachedTerminalCount) {
          conv._cachedTerminalCount = count;
          changed = true;
        }
        if (!changed || !mounted) return;
        setState(() {});
      };
      conv.model!.addListener(conv._modelListener!);
    } else if (conv._modelListener == null) {
      conv._cachedCwdLabel = conv.model!.cwdBase;
      conv._cachedTerminalDocked = conv.model!.terminalDocked;
      conv._cachedTerminalSession = conv.model!.activeTerminalSession;
      conv._cachedTerminalCount = conv.model!.terminalDeck.length;
      conv._modelListener = () {
        final model = conv.model;
        if (model == null) return;
        bool changed = false;
        final cwd = model.cwdBase;
        if (cwd != conv._cachedCwdLabel) {
          conv._cachedCwdLabel = cwd;
          changed = true;
        }
        final docked = model.terminalDocked;
        if (docked != conv._cachedTerminalDocked) {
          conv._cachedTerminalDocked = docked;
          changed = true;
        }
        final session = model.activeTerminalSession;
        if (!identical(session, conv._cachedTerminalSession)) {
          conv._cachedTerminalSession = session;
          changed = true;
        }
        final count = model.terminalDeck.length;
        if (count != conv._cachedTerminalCount) {
          conv._cachedTerminalCount = count;
          changed = true;
        }
        if (!changed || !mounted) return;
        setState(() {});
      };
      conv.model!.addListener(conv._modelListener!);
    }
    conv._cachedCwdLabel = conv.model!.cwdBase;
    return conv.model!;
  }

  Widget _chatWidget(_Conversation conv,
      {int? dragIndex, bool showDivider = true, required bool dockTerminals}) {
    final agent = conv.agent;
    final model = _ensureModel(conv);

    final chat = ChatScreen(
      key: ValueKey(conv.id),
      controller: conv.controller,
      model: model,
      title: agent.name,
      onClose: () => _closeConversation(conv.id),
      embedded: true,
      dragIndex: dragIndex,
      dockTerminals: dockTerminals,
    );

    // Add a subtle divider on the right to visually separate columns.
    return Container(
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surface,
        border: showDivider
            ? const Border(
                right: BorderSide(color: bgCardAlt, width: 1.0),
              )
            : null,
      ),
      child: chat,
    );
  }

  // Strip layout (horizontal reorderable list)
  Widget _buildStrip() {
    return Scrollbar(
      thumbVisibility: true,
      controller: _hController,
      notificationPredicate: (_) => true,
      child: ReorderableListView.builder(
        scrollController: _hController,
        scrollDirection: Axis.horizontal,
        itemCount: _conversations.length,
        buildDefaultDragHandles: false,
        proxyDecorator: (child, index, animation) => Material(
          color: Colors.transparent,
          child: child,
        ),
        footer: const SizedBox(width: 12),
        itemBuilder: (context, index) {
          final conv = _conversations[index];
          return SizedBox(
            key: ValueKey(conv.id),
            width: 420,
            child: _chatWidget(conv, dragIndex: index, dockTerminals: false),
          );
        },
        onReorder: (oldIndex, newIndex) {
          if (newIndex > oldIndex) newIndex--;
          setState(() {
            final item = _conversations.removeAt(oldIndex);
            _conversations.insert(newIndex, item);
          });
          _saveOrder();
        },
      ),
    );
  }

  // Paged view for small screens.
  Widget _buildPagedView() {
    if (_conversations.isEmpty) return _emptyState();
    return PageView.builder(
      itemCount: _conversations.length,
      itemBuilder: (context, index) {
        final conv = _conversations[index];
        return _chatWidget(conv, dockTerminals: false);
      },
    );
  }

  Widget _buildFocusLayout(AgentsRepository repo, double totalWidth) {
    final conv = _ensureSelectedConversation();
    if (conv == null) {
      return _emptyState();
    }

    final scheme = Theme.of(context).colorScheme;
    final model = _ensureModel(conv);
    final deck = model.terminalDeck;
    final showTerminal = deck.docked && deck.activeSession != null;
    final chatPane = _chatWidget(conv, showDivider: false, dockTerminals: true);
    return Row(
      children: [
        Container(
          width: _sidebarWidth,
          decoration: BoxDecoration(
            color: scheme.surfaceContainerHighest,
            border: const Border(
              right: BorderSide(color: bgCardAlt, width: 1.0),
            ),
          ),
          child: SafeArea(
            maintainBottomViewPadding: true,
            child: Column(
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
                  child: Align(
                    alignment: Alignment.centerLeft,
                    child: Text(
                      'Conversations',
                      style: Theme.of(context)
                          .textTheme
                          .titleMedium
                          ?.copyWith(fontWeight: FontWeight.w600),
                    ),
                  ),
                ),
                Expanded(
                  child: ReorderableListView.builder(
                    itemCount: _conversations.length,
                    padding: const EdgeInsets.symmetric(horizontal: 8),
                    buildDefaultDragHandles: false,
                    header: const SizedBox(height: 4),
                    itemBuilder: (context, index) {
                      final item = _conversations[index];
                      final selected = item.id == _selectedConversationId;
                      final model = _ensureModel(item);
                      final cwd = model.cwdBase;
                      final titleText = (cwd != null && cwd.isNotEmpty)
                          ? '${item.agent.name} • $cwd'
                          : item.agent.name;
                      return Container(
                        key: ValueKey(item.id),
                        margin: const EdgeInsets.symmetric(vertical: 4),
                        decoration: BoxDecoration(
                          color: selected ? scheme.surface : Colors.transparent,
                          borderRadius: BorderRadius.circular(10),
                          border: Border.all(
                            color: selected
                                ? tweetBlue.withValues(alpha: 0.6)
                                : bgCardAlt,
                          ),
                        ),
                        child: ListTile(
                          dense: true,
                          title: Text(
                            titleText,
                            overflow: TextOverflow.ellipsis,
                          ),
                          onTap: () => _selectConversation(item.id),
                          trailing: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              IconButton(
                                icon: const Icon(Icons.close, size: 18),
                                tooltip: 'Close',
                                onPressed: () => _closeConversation(item.id),
                              ),
                              ReorderableDragStartListener(
                                index: index,
                                child:
                                    const Icon(Icons.drag_indicator, size: 18),
                              ),
                            ],
                          ),
                        ),
                      );
                    },
                    onReorder: (oldIndex, newIndex) {
                      if (newIndex > oldIndex) newIndex--;
                      setState(() {
                        final item = _conversations.removeAt(oldIndex);
                        _conversations.insert(newIndex, item);
                      });
                      _saveOrder();
                    },
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 12, 16, 20),
                  child: SizedBox(
                    width: double.infinity,
                    child: ElevatedButton.icon(
                      icon: const Icon(Icons.add),
                      label: const Text('New Conversation'),
                      onPressed:
                          repo.agents.isEmpty ? null : () => _pickAgent(repo),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
        Expanded(
          child: showTerminal
              ? _buildChatWithTerminal(
                  chatPane: chatPane,
                  deck: deck,
                  model: model,
                  scheme: scheme,
                  totalWidth: totalWidth,
                )
              : chatPane,
        ),
      ],
    );
  }

  Widget _buildChatWithTerminal({
    required Widget chatPane,
    required TerminalDeck deck,
    required ChatModel model,
    required ColorScheme scheme,
    required double totalWidth,
  }) {
    final availableWidth =
        math.max(1.0, totalWidth - _sidebarWidth - _terminalDragWidth);
    final fraction =
        _terminalPaneFraction.clamp(_terminalFractionMin, _terminalFractionMax);
    final chatFlex = math.max(1, ((1 - fraction) * 1000).round());
    final terminalFlex = math.max(1, (fraction * 1000).round());

    return Row(
      children: [
        Expanded(
          flex: chatFlex,
          child: chatPane,
        ),
        GestureDetector(
          behavior: HitTestBehavior.opaque,
          onHorizontalDragUpdate: (details) {
            final total = availableWidth;
            if (total <= 0) return;
            setState(() {
              final updated = (_terminalPaneFraction - details.delta.dx / total)
                  .clamp(_terminalFractionMin, _terminalFractionMax);
              _terminalPaneFraction = updated;
            });
          },
          child: MouseRegion(
            cursor: SystemMouseCursors.resizeLeftRight,
            child: Container(
              width: _terminalDragWidth,
              color: scheme.surface,
              child: Center(
                child: Container(
                  width: 1,
                  height: double.infinity,
                  color: bgCardAlt,
                ),
              ),
            ),
          ),
        ),
        Expanded(
          flex: terminalFlex,
          child: ChangeNotifierProvider.value(
            value: deck,
            child: DockedTerminalPane(
              onClose: () => model.closeDockedTerminal(),
              padding: EdgeInsets.zero,
            ),
          ),
        ),
      ],
    );
  }

  _Conversation? _ensureSelectedConversation() {
    if (_conversations.isEmpty) {
      return null;
    }
    if (_selectedConversationId != null) {
      for (final conv in _conversations) {
        if (conv.id == _selectedConversationId) {
          return conv;
        }
      }
    }
    final fallback = _conversations.first;
    if (_selectedConversationId != fallback.id) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (!mounted) return;
        setState(() {
          _selectedConversationId = fallback.id;
        });
      });
    }
    return fallback;
  }

  void _selectConversation(String id) {
    if (_selectedConversationId == id) return;
    setState(() {
      _selectedConversationId = id;
    });
  }

  Widget _emptyState() {
    return Center(
      child: TextButton.icon(
        icon: const Icon(Icons.settings),
        label: const Text('Add an AI Agent to begin'),
        onPressed: () => Navigator.of(context).push(
          MaterialPageRoute(
              builder: (_) => _ManageAgentsScreen(
                    onAgentUpdated: _handleAgentUpdated,
                  )),
        ),
      ),
    );
  }

  Future<void> _loadOrder() async {
    final prefs = await SharedPreferences.getInstance();
    final stored = prefs.getStringList('column_order_v1') ?? [];
    final generation = ++_loadGeneration;

    // We need the AgentsRepository to be available via context after build.
    // Delay reordering until first frame to ensure repository is loaded.
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      if (!mounted || _conversations.isNotEmpty) {
        return;
      }
      if (_suppressRestores || generation != _loadGeneration) {
        return;
      }
      final repo = context.read<AgentsRepository>();
      final sessionsMap = await _fetchActiveSessions(repo);
      if (_suppressRestores || generation != _loadGeneration || !mounted) {
        return;
      }
      final List<_Conversation> convs = [];

      // First add sessions in the order stored locally if they still exist.
      for (final s in stored) {
        try {
          final obj = jsonDecode(s) as Map<String, dynamic>;
          final endpoint = obj['endpoint'] as String?;
          final sessionId = obj['session_id'] as String?;
          if (endpoint == null || sessionId == null) continue;
          final active = sessionsMap[endpoint];
          if (active != null && active.remove(sessionId)) {
            final agent = repo.agents.firstWhere((a) => a.endpoint == endpoint,
                orElse: () => AgentConfig(name: endpoint, endpoint: endpoint));
            convs.add(_Conversation(
                id: 'c${_idCounter++}', agent: agent, sessionId: sessionId));
          }
        } catch (_) {
          continue;
        }
      }

      // Append any new sessions not seen before.
      for (final entry in sessionsMap.entries) {
        final endpoint = entry.key;
        final agent = repo.agents.firstWhere((a) => a.endpoint == endpoint,
            orElse: () => AgentConfig(name: endpoint, endpoint: endpoint));
        for (final sessionId in entry.value) {
          convs.add(_Conversation(
              id: 'c${_idCounter++}', agent: agent, sessionId: sessionId));
        }
      }

      if (convs.isNotEmpty) {
        if (_suppressRestores || generation != _loadGeneration || !mounted) {
          for (final conv in convs) {
            conv.dispose();
          }
          return;
        }
        setState(() {
          _conversations
            ..clear()
            ..addAll(convs);
          _selectedConversationId = convs.first.id;
        });
        _saveOrder();
      }
    });
  }

  Future<void> _saveOrder() async {
    final prefs = await SharedPreferences.getInstance();
    final list = _conversations
        .map((c) => jsonEncode({
              'endpoint': c.agent.endpoint,
              'session_id': c.sessionId,
            }))
        .toList();
    await prefs.setStringList('column_order_v1', list);
  }

  Future<void> _loadLayoutMode() async {
    final prefs = await SharedPreferences.getInstance();
    final stored = prefs.getString('layout_mode_v1');
    if (stored == null) return;
    for (final mode in DeckLayoutMode.values) {
      if (mode.name == stored && mode != _layoutMode) {
        if (!mounted) return;
        setState(() {
          _layoutMode = mode;
        });
        break;
      }
    }
  }

  Future<void> _saveLayoutMode() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('layout_mode_v1', _layoutMode.name);
  }

  void _toggleLayoutMode() {
    final next = _layoutMode == DeckLayoutMode.deck
        ? DeckLayoutMode.focus
        : DeckLayoutMode.deck;
    setState(() {
      _layoutMode = next;
    });
    if (next == DeckLayoutMode.deck) {
      for (final conv in _conversations) {
        conv.model?.setTerminalDocked(false);
      }
    } else {
      for (final conv in _conversations) {
        final model = conv.model;
        if (model == null) continue;
        if (model.terminalDeck.length > 0) {
          model.setTerminalDocked(true);
        }
      }
    }
    _saveLayoutMode();
  }

  bool get _hasArchivableConversations =>
      _conversations.any((c) => c.sessionId != null && c.sessionId!.isNotEmpty);

  Future<void> _archiveAllConversations() async {
    if (_archivingAll) return;
    final targets = _conversations
        .where((c) => c.sessionId != null && c.sessionId!.isNotEmpty)
        .toList();
    if (targets.isEmpty) return;
    setState(() {
      _archivingAll = true;
      _suppressRestores = true;
      _loadGeneration++;
    });
    for (final conv in targets) {
      final sessionId = conv.sessionId;
      if (sessionId == null || sessionId.isEmpty) continue;
      try {
        await conv.client.archiveSession(sessionId);
      } catch (_) {}
    }
    if (!mounted) {
      for (final conv in targets) {
        conv.dispose();
      }
      _suppressRestores = false;
      return;
    }
    setState(() {
      final toRemove = targets.where(_conversations.contains).toList();
      final removeSet = toRemove.toSet();
      bool selectionStale = false;
      for (final conv in toRemove) {
        if (_selectedConversationId == conv.id) {
          selectionStale = true;
        }
        conv.dispose();
      }
      _conversations.removeWhere(removeSet.contains);
      if (_conversations.isEmpty) {
        _selectedConversationId = null;
      } else if (selectionStale ||
          (_selectedConversationId != null &&
              !_conversations.any((c) => c.id == _selectedConversationId))) {
        _selectedConversationId = _conversations.first.id;
      }
      _archivingAll = false;
      _suppressRestores = false;
    });
    await _saveOrder();
  }

  /// Query each configured agent for its active (non-archived) sessions.
  Future<Map<String, List<String>>> _fetchActiveSessions(
      AgentsRepository repo) async {
    if (_suppressRestores) {
      return {};
    }
    final Map<String, List<String>> result = {};
    for (final agent in repo.agents) {
      // Reuse an existing client if we already have a conversation for this
      // agent so the WebSocket stays connected. Otherwise create a temporary
      // one just for listing sessions.
      RemoteAgentClient? client;
      for (final c in _conversations) {
        final sameEndpoint = c.agent.endpoint == agent.endpoint;
        final sameToken = c.agent.token == agent.token;
        if (sameEndpoint && sameToken) {
          client = c.client;
          break;
        }
      }
      final bool ownClient = client == null;
      client ??=
          RemoteAgentClient(agent.endpoint, token: agent.token, debug: true);
      try {
        final sessions = await client.listSessions();
        for (final sess in sessions) {
          if (sess is Map && sess['id'] is String) {
            final archived = sess['archived'] as bool? ?? false;
            if (!archived) {
              result
                  .putIfAbsent(agent.endpoint, () => [])
                  .add(sess['id'] as String);
            }
          }
        }
      } catch (_) {
        // Ignore errors per agent to keep others loading.
      } finally {
        // Only dispose if we created a temporary client above – existing
        // conversations keep their connection alive.
        if (ownClient) client.dispose();
      }
    }
    return result;
  }

  Future<void> _pickAgent(AgentsRepository repo) async {
    final chosen = await showDialog<AgentConfig>(
      context: context,
      builder: (context) {
        return SimpleDialog(
          title: const Text('Choose agent'),
          children: [
            for (final agent in repo.agents)
              SimpleDialogOption(
                onPressed: () => Navigator.of(context).pop(agent),
                child: Text(agent.name),
              ),
          ],
        );
      },
    );
    if (chosen != null) {
      _startConversation(chosen);
    }
  }

  // Called when an existing agent has been modified so we can refresh
  // corresponding conversations.
  void _handleAgentUpdated(AgentConfig oldAgent, AgentConfig updatedAgent) {
    bool needsSave = false;
    for (final conv in _conversations) {
      if (identical(conv.agent, oldAgent)) {
        conv.resetAgent(updatedAgent);
        needsSave = true;
      }
    }
    if (needsSave) {
      setState(() {});
      _saveOrder();
    }
  }

  // -----------------------------------------------------------------------
  // App lifecycle handling -------------------------------------------------
  // -----------------------------------------------------------------------

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      // When returning to the foreground ensure all conversations have a live
      // WebSocket.  We ignore errors because the UI will surface connection
      // problems through its existing listeners.
      for (final conv in _conversations) {
        conv.client.connect().catchError((_) {});
      }
    }
    super.didChangeAppLifecycleState(state);
  }
}

// ---------------------------------------------------------------------------
// Manage agents -------------------------------------------------------------
// ---------------------------------------------------------------------------

class _ManageAgentsScreen extends StatelessWidget {
  const _ManageAgentsScreen({Key? key, this.onAgentUpdated}) : super(key: key);

  final void Function(AgentConfig oldAgent, AgentConfig updatedAgent)?
      onAgentUpdated;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Manage Agents')),
      body: _AgentsListView(onAgentUpdated: onAgentUpdated),
      floatingActionButton: FloatingActionButton(
        onPressed: () => _showAddDialog(context),
        child: const Icon(Icons.add),
      ),
    );
  }

  Future<void> _showAddDialog(BuildContext context) async {
    final formKey = GlobalKey<FormState>();
    final nameCtrl = TextEditingController();
    final urlCtrl = TextEditingController();
    final tokenCtrl = TextEditingController();

    await showDialog<void>(
      context: context,
      builder: (context) {
        return AlertDialog(
          title: const Text('Add AI Agent'),
          content: Form(
            key: formKey,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  TextFormField(
                    controller: nameCtrl,
                    decoration: const InputDecoration(labelText: 'Name'),
                    validator: (v) => (v == null || v.trim().isEmpty)
                        ? 'Please enter a name'
                        : null,
                  ),
                  TextFormField(
                    controller: urlCtrl,
                    decoration:
                        const InputDecoration(labelText: 'WebSocket URL'),
                    validator: (v) => (v == null || v.trim().isEmpty)
                        ? 'Please enter an endpoint'
                        : null,
                  ),
                  TextFormField(
                    controller: tokenCtrl,
                    decoration: const InputDecoration(
                        labelText: 'Auth token (optional)'),
                  ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
                onPressed: () => Navigator.of(context).pop(),
                child: const Text('Cancel')),
            ElevatedButton(
              onPressed: () async {
                if (formKey.currentState?.validate() ?? false) {
                  final repo = context.read<AgentsRepository>();
                  await repo.addAgent(AgentConfig(
                    name: nameCtrl.text.trim(),
                    endpoint: urlCtrl.text.trim(),
                    token: tokenCtrl.text.trim().isEmpty
                        ? null
                        : tokenCtrl.text.trim(),
                  ));
                  // Only close the dialog after the preferences have been
                  // written to disk to ensure persistence across restarts.
                  if (context.mounted) {
                    Navigator.of(context).pop();
                  }
                }
              },
              child: const Text('Add'),
            ),
          ],
        );
      },
    );
  }
}

class _AgentsListView extends StatelessWidget {
  const _AgentsListView({this.onAgentUpdated});

  final void Function(AgentConfig oldAgent, AgentConfig updatedAgent)?
      onAgentUpdated;

  // Shows a dialog that allows editing the given [agent].
  Future<void> _showEditDialog(
      BuildContext context, AgentsRepository repo, AgentConfig agent) async {
    final formKey = GlobalKey<FormState>();
    final nameCtrl = TextEditingController(text: agent.name);
    final urlCtrl = TextEditingController(text: agent.endpoint);
    final tokenCtrl = TextEditingController(text: agent.token ?? '');

    await showDialog<void>(
      context: context,
      builder: (context) {
        return AlertDialog(
          title: const Text('Edit AI Agent'),
          content: Form(
            key: formKey,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  TextFormField(
                    controller: nameCtrl,
                    decoration: const InputDecoration(labelText: 'Name'),
                    validator: (v) => (v == null || v.trim().isEmpty)
                        ? 'Please enter a name'
                        : null,
                  ),
                  TextFormField(
                    controller: urlCtrl,
                    decoration:
                        const InputDecoration(labelText: 'WebSocket URL'),
                    validator: (v) => (v == null || v.trim().isEmpty)
                        ? 'Please enter an endpoint'
                        : null,
                  ),
                  TextFormField(
                    controller: tokenCtrl,
                    decoration: const InputDecoration(
                        labelText: 'Auth token (optional)'),
                  ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
                onPressed: () => Navigator.of(context).pop(),
                child: const Text('Cancel')),
            ElevatedButton(
              onPressed: () async {
                if (formKey.currentState?.validate() ?? false) {
                  final updated = AgentConfig(
                    name: nameCtrl.text.trim(),
                    endpoint: urlCtrl.text.trim(),
                    token: tokenCtrl.text.trim().isEmpty
                        ? null
                        : tokenCtrl.text.trim(),
                  );
                  await repo.updateAgent(agent, updated);
                  onAgentUpdated?.call(agent, updated);
                  if (context.mounted) {
                    Navigator.of(context).pop();
                  }
                }
              },
              child: const Text('Save'),
            ),
          ],
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final repo = context.watch<AgentsRepository>();

    if (repo.agents.isEmpty) {
      return const Center(child: Text('No agents configured.'));
    }

    return ListView.builder(
      itemCount: repo.agents.length,
      itemBuilder: (context, index) {
        final agent = repo.agents[index];
        return ListTile(
          title: Text(agent.name),
          subtitle: Text(agent.endpoint),
          trailing: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              IconButton(
                icon: const Icon(Icons.edit),
                tooltip: 'Edit',
                onPressed: () => _showEditDialog(context, repo, agent),
              ),
              IconButton(
                icon: const Icon(Icons.delete),
                tooltip: 'Delete',
                onPressed: () async {
                  // Ensure deletion persists before the ListTile disappears.
                  await repo.removeAgent(agent);
                },
              ),
            ],
          ),
          onTap: () => _showEditDialog(context, repo, agent),
        );
      },
    );
  }
}
