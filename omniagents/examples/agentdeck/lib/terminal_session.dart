import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:xterm/xterm.dart';

import 'remote_agent_client.dart';

class RemoteTerminalSession extends ChangeNotifier {
  RemoteTerminalSession(this.client);

  final RemoteAgentClient client;
  Terminal? terminal;
  TerminalController? controller;
  WebSocketChannel? _channel;
  StreamSubscription? _subscription;
  bool connecting = false;
  bool connected = false;
  bool exited = false;
  int? exitCode;
  String? error;
  String? cwd;
  String? terminalId;
  String? token;
  String? sessionHint;

  Future<void> connect({String? preferredCwd}) async {
    if (connecting) return;
    if (connected && !exited) return;
    final sid = client.sessionId ?? sessionHint;
    if (sid == null || sid.isEmpty) {
      error = 'Start a session before opening the terminal.';
      notifyListeners();
      return;
    }
    connecting = true;
    exited = false;
    exitCode = null;
    error = null;
    notifyListeners();
    terminal ??= Terminal(maxLines: 10000);
    controller ??= TerminalController();
    terminal!.onOutput = (data) {
      final channel = _channel;
      if (channel == null) return;
      if (data.isEmpty) return;
      final bytes = utf8.encode(data);
      if (bytes.isEmpty) return;
      final payload = jsonEncode({
        'type': 'input',
        'data': base64Encode(bytes),
      });
      channel.sink.add(payload);
    };
    terminal!.onResize = (width, height, _, __) {
      final channel = _channel;
      if (channel == null) return;
      final payload = jsonEncode({
        'type': 'resize',
        'cols': width,
        'rows': height,
      });
      channel.sink.add(payload);
    };
    final args = <String, dynamic>{};
    if (preferredCwd != null && preferredCwd.isNotEmpty) {
      args['cwd'] = preferredCwd;
    }
    final response = await _createTerminal(args);
    if (response == null) {
      connecting = false;
      notifyListeners();
      return;
    }
    terminalId = response['terminal_id']?.toString();
    token = (response['terminal_token'] ?? response['token'])?.toString();
    cwd = response['cwd']?.toString();
    final path = response['path']?.toString() ?? '/ws/terminal';
    final sessionFromServer = response['session_id']?.toString() ?? sid;
    if (terminalId == null ||
        terminalId!.isEmpty ||
        token == null ||
        token!.isEmpty) {
      error = 'Terminal setup failed.';
      connecting = false;
      notifyListeners();
      return;
    }
    sessionHint = sessionFromServer;
    final Uri baseUri;
    try {
      baseUri = Uri.parse(client.endpoint);
    } catch (_) {
      error = 'Invalid server endpoint.';
      connecting = false;
      notifyListeners();
      return;
    }
    final query = <String, String>{
      'session_id': sessionFromServer,
      'terminal_id': terminalId!,
      'terminal_token': token!,
    };
    if (client.token != null && client.token!.isNotEmpty) {
      query['token'] = client.token!;
    }
    final uri = baseUri.replace(path: path, queryParameters: query);
    await _subscription?.cancel();
    _subscription = null;
    final previousChannel = _channel;
    _channel = null;
    if (previousChannel != null) {
      try {
        previousChannel.sink.close();
      } catch (_) {}
    }
    try {
      _channel = WebSocketChannel.connect(uri);
    } catch (e) {
      error = 'Terminal connection failed: $e';
      connecting = false;
      notifyListeners();
      return;
    }
    connected = true;
    connecting = false;
    notifyListeners();
    _subscription = _channel!.stream.listen(
      (event) {
        final Map<String, dynamic>? message = _decodeMessage(event);
        if (message == null) return;
        final type = message['type']?.toString();
        if (type == 'output') {
          final data = message['data']?.toString();
          if (data == null) return;
          try {
            final bytes = base64Decode(data);
            final text = utf8.decode(bytes, allowMalformed: true);
            terminal?.write(text);
          } catch (_) {}
        } else if (type == 'exit') {
          exitCode = _coerceInt(message['code']);
          exited = true;
          connected = false;
          notifyListeners();
        }
      },
      onError: (errorEvent) {
        error = errorEvent.toString();
        connected = false;
        connecting = false;
        notifyListeners();
      },
      onDone: () {
        connected = false;
        if (!exited) exited = true;
        _channel = null;
        notifyListeners();
      },
    );
  }

  Future<Map<String, dynamic>?> _createTerminal(
      Map<String, dynamic> args) async {
    try {
      final result = await client.serverCall('terminal.create', args: args);
      if (result.isEmpty) {
        error = 'Server did not return terminal info.';
        return null;
      }
      return result;
    } catch (e) {
      error = 'Terminal creation failed: $e';
      return null;
    }
  }

  Map<String, dynamic>? _decodeMessage(dynamic event) {
    if (event is Map) {
      try {
        return event.cast<String, dynamic>();
      } catch (_) {
        return null;
      }
    }
    if (event is String) {
      try {
        final decoded = jsonDecode(event);
        if (decoded is Map) {
          return decoded.cast<String, dynamic>();
        }
      } catch (_) {}
    }
    return null;
  }

  int? _coerceInt(dynamic value) {
    if (value == null) return null;
    try {
      return int.parse(value.toString());
    } catch (_) {
      return null;
    }
  }

  void close() {
    _subscription?.cancel();
    _subscription = null;
    final channel = _channel;
    if (channel != null) {
      try {
        channel.sink.add(jsonEncode({'type': 'close'}));
      } catch (_) {}
      try {
        channel.sink.close();
      } catch (_) {}
    }
    _channel = null;
    connected = false;
    connecting = false;
    notifyListeners();
  }

  void markDisconnected() {
    connected = false;
    notifyListeners();
  }

  @override
  void dispose() {
    close();
    terminal = null;
    controller?.dispose();
    controller = null;
    super.dispose();
  }
}

class TerminalDeck extends ChangeNotifier {
  TerminalDeck(this.client);

  final RemoteAgentClient client;

  final List<RemoteTerminalSession> _sessions = [];
  final List<VoidCallback> _sessionListeners = [];
  int _activeIndex = 0;
  bool docked = false;
  String? sessionHint;
  String? defaultCwd;

  List<RemoteTerminalSession> get sessions => List.unmodifiable(_sessions);
  int get length => _sessions.length;
  int get activeIndex =>
      _sessions.isEmpty ? 0 : _activeIndex.clamp(0, _sessions.length - 1);
  RemoteTerminalSession? get activeSession =>
      _sessions.isEmpty ? null : _sessions[activeIndex];

  Future<RemoteTerminalSession> ensureActive({String? preferredCwd}) async {
    if (_sessions.isEmpty) {
      return addSession(preferredCwd: preferredCwd);
    }
    final session = activeSession!;
    if (!session.connected && !session.connecting) {
      await session.connect(preferredCwd: preferredCwd ?? defaultCwd);
    }
    return session;
  }

  Future<RemoteTerminalSession> addSession({String? preferredCwd}) async {
    final session = RemoteTerminalSession(client);
    session.sessionHint = sessionHint;
    _sessions.add(session);
    _sessionListeners.add(_attachSession(session));
    _activeIndex = _sessions.length - 1;
    defaultCwd = preferredCwd ?? defaultCwd;
    notifyListeners();
    await session.connect(preferredCwd: preferredCwd ?? defaultCwd);
    return session;
  }

  VoidCallback _attachSession(RemoteTerminalSession session) {
    void listener() {
      notifyListeners();
    }

    session.addListener(listener);
    return () {
      session.removeListener(listener);
    };
  }

  void closeSession(int index) {
    if (index < 0 || index >= _sessions.length) {
      return;
    }
    final session = _sessions.removeAt(index);
    final disposer = _sessionListeners.removeAt(index);
    disposer();
    session.close();
    session.dispose();
    if (_sessions.isEmpty) {
      _activeIndex = 0;
      docked = false;
    } else if (_activeIndex >= _sessions.length) {
      _activeIndex = _sessions.length - 1;
    }
    notifyListeners();
  }

  void setActiveIndex(int index) {
    if (_sessions.isEmpty) return;
    final clamped = index.clamp(0, _sessions.length - 1);
    if (_activeIndex == clamped) return;
    _activeIndex = clamped;
    notifyListeners();
  }

  void setDocked(bool value) {
    if (docked == value) return;
    docked = value;
    notifyListeners();
  }

  void updateSessionHint(String? value) {
    if (sessionHint == value) return;
    sessionHint = value;
    for (final session in _sessions) {
      session.sessionHint = value;
    }
  }

  void markDisconnected() {
    for (final session in _sessions) {
      session.markDisconnected();
    }
    notifyListeners();
  }

  void clear() {
    for (final disposer in _sessionListeners) {
      disposer();
    }
    _sessionListeners.clear();
    for (final session in _sessions) {
      session.close();
      session.dispose();
    }
    _sessions.clear();
    _activeIndex = 0;
    docked = false;
    notifyListeners();
  }

  @override
  void dispose() {
    clear();
    super.dispose();
  }
}

extension TerminalDeckTitles on TerminalDeck {
  String titleFor(int index) {
    final number = index + 1;
    return number == 1 ? 'Terminal' : 'Terminal $number';
  }
}
