import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'chat_screen.dart';
import 'remote_agent_client.dart';
import 'agents_repository.dart';
import 'theme/app_colors.dart';

class MultiColumnScreen extends StatefulWidget {
  const MultiColumnScreen({super.key});

  @override
  State<MultiColumnScreen> createState() => _MultiColumnScreenState();
}

class _Conversation {
  _Conversation({required this.id, required this.agent, this.sessionId})
      : client = RemoteAgentClient(
          agent.endpoint,
          token: agent.token,
          debug: true,
        );

  final String id;

  // Hold mutable reference so we can swap in updated config
  AgentConfig agent;
  String? sessionId;

  // Client needs to be recreated if agent config changes.
  late RemoteAgentClient client;

  // Persist ChatModel so that UI rebuilds (e.g., during layout changes)
  // don't lose state.
  ChatModel? model;

  /// Re-initialises the connection when the [agent] configuration changes.
  void resetAgent(AgentConfig newAgent) {
    // Drop the existing model reference; the Provider tree will dispose it
    // automatically when the widget subtree is rebuilt.
    model = null;

    // Dispose the current client connection.
    client.dispose();

    // Update agent reference and create a new client.
    agent = newAgent;
    client = RemoteAgentClient(
      newAgent.endpoint,
      token: newAgent.token,
      debug: true,
    );

    // If we already have a session id, let the new client know so that
    // history continues seamlessly.
    if (sessionId != null) {
      client.resumeSession(sessionId!);
    }
  }

  // Close the underlying client connection.
  void dispose() {
    model?.dispose();
    client.dispose();
  }
}

class _MultiColumnScreenState extends State<MultiColumnScreen>
    with WidgetsBindingObserver {
  final List<_Conversation> _conversations = [];
  int _idCounter = 0;

  final ScrollController _hController = ScrollController();

  @override
  void initState() {
    super.initState();
    // Observe app lifecycle changes so we can reconnect when returning to
    // the foreground.
    WidgetsBinding.instance.addObserver(this);
    _loadOrder();
  }

  void _startConversation(AgentConfig agent) {
    setState(() {
      _conversations.add(_Conversation(id: 'c${_idCounter++}', agent: agent));
    });
    _saveOrder();
  }

  void _closeConversation(String id) {
    setState(() {
      final conv = _conversations.firstWhere((c) => c.id == id);
      if (conv.sessionId != null) {
        // Archive the session on the server so it no longer appears in lists.
        conv.client.archiveSession(conv.sessionId!).catchError((_) {});
      }
      conv.dispose();
      _conversations.remove(conv);
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
            tooltip: 'New Conversation',
            icon: const Icon(Icons.add),
            onPressed: repo.agents.isEmpty ? null : () => _pickAgent(repo),
          ),
        ],
      ),
      body: LayoutBuilder(
        builder: (context, constraints) {
          final width = constraints.maxWidth;
          if (width < 600) {
            // Phones – single column pager.
            return repo.agents.isEmpty && _conversations.isEmpty
                ? _emptyState()
                : _buildPagedView();
          } else {
            // Tablets & Desktop – horizontal strip similar to TweetDeck.
            return repo.agents.isEmpty && _conversations.isEmpty
                ? _emptyState()
                : _buildStrip();
          }
        },
      ),
    );
  }

  // ---------------------------
  // Layout helpers ------------
  // ---------------------------

  Widget _chatWidget(_Conversation conv, {int? dragIndex}) {
    final agent = conv.agent;

    // Lazily create the ChatModel once per conversation.
    conv.model ??= ChatModel(conv.client, sessionId: conv.sessionId,
        onRunStarted: (runId, sessionId) {
      if (sessionId != null) {
        conv.sessionId = sessionId;
        _saveOrder();
      }
    });

    final chat = ChatScreen(
      key: ValueKey(conv.id),
      client: conv.client,
      model: conv.model,
      title: agent.name,
      onClose: () => _closeConversation(conv.id),
      embedded: true,
      dragIndex: dragIndex,
    );

    // Add a subtle divider on the right to visually separate columns.
    return Container(
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surface,
        border: const Border(
          right: BorderSide(color: bgCardAlt, width: 1.0),
        ),
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
            child: _chatWidget(conv, dragIndex: index),
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
        return _chatWidget(conv);
      },
    );
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

    // We need the AgentsRepository to be available via context after build.
    // Delay reordering until first frame to ensure repository is loaded.
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      final repo = context.read<AgentsRepository>();
      final sessionsMap = await _fetchActiveSessions(repo);
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
        setState(() {
          _conversations
            ..clear()
            ..addAll(convs);
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

  /// Query each configured agent for its active (non-archived) sessions.
  Future<Map<String, List<String>>> _fetchActiveSessions(
      AgentsRepository repo) async {
    final Map<String, List<String>> result = {};
    for (final agent in repo.agents) {
      // Reuse an existing client if we already have a conversation for this
      // agent so the WebSocket stays connected. Otherwise create a temporary
      // one just for listing sessions.
      RemoteAgentClient? client;
      for (final c in _conversations) {
        if (c.agent.endpoint == agent.endpoint) {
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
              result.putIfAbsent(agent.endpoint, () => []).add(sess['id'] as String);
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
  const _ManageAgentsScreen({super.key, this.onAgentUpdated});

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
