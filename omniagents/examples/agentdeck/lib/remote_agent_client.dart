import 'dart:async';
import 'dart:convert';
// ignore: avoid_web_libraries_in_flutter (only used for standalone/mobile, web will use JS WebSocket)

import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:uuid/uuid.dart';
import 'package:flutter/foundation.dart';

import 'models.dart';

class RemoteRpcError implements Exception {
  RemoteRpcError(this.code, this.message, [this.data]);

  final int? code;
  final String message;
  final dynamic data;

  @override
  String toString() {
    if (code != null) {
      return 'RemoteRpcError($code, $message)';
    }
    return 'RemoteRpcError($message)';
  }
}

/// Lightweight client that speaks JSON-RPC 2.0 over WebSockets.
class RemoteAgentClient {
  RemoteAgentClient(this.endpoint,
      {this.token, this.debug = false, this.label});

  /// WebSocket URL of the OmniAgents server (for example ws://localhost:8000/ws).
  final String endpoint;

  /// Optional bearer-token – will be appended as ?token=X or &token=X.
  final String? token;

  final bool debug;
  final String? label;

  WebSocketChannel? _channel;
  final _uuid = const Uuid();
  Map<String, dynamic>? _initialVariables;
  String? get sessionId => _sessionId;
  bool get hasSession => _sessionId != null && _sessionId!.isNotEmpty;

  // ---------------------------------------------------------------------------
  //  Public event stream -------------------------------------------------------
  // ---------------------------------------------------------------------------
  final _eventsController = StreamController<RemoteEvent>.broadcast();
  Stream<RemoteEvent> get events => _eventsController.stream;

  // Keep track of pending RPC calls by call_id so that we can complete the Future.
  final Map<String, Completer<dynamic>> _pending = {};

  // Session id returned by server after first start_run.
  String? _sessionId;

  /// If you already know the session id (e.g. after app restart), call this
  /// before startRun() so that further prompts continue the same session.
  void resumeSession(String sessionId) {
    if (debug) {
      debugPrint('${_tag()} resumeSession -> $sessionId'
          ' (was ${_sessionId ?? 'none'})');
    }
    _sessionId = sessionId;
  }

  bool _disposed = false;

  Future<void> connect() async {
    if (_channel != null) return;

    var url = endpoint;
    if (token != null && token!.isNotEmpty) {
      url += endpoint.contains('?') ? '&' : '?';
      url += 'token=${Uri.encodeComponent(token!)}';
    }

    if (debug) debugPrint('${_tag()} [ws] Connecting to $url');
    try {
      _channel = WebSocketChannel.connect(Uri.parse(url));

      // Send WebSocket ping frames periodically to keep the connection
      // alive on servers/load-balancers that close idle sockets.  We cast
      // to dynamic so this compiles on web where the field may not be
      // present (and where browsers handle pings automatically).
      try {
        (_channel! as dynamic).pingInterval = const Duration(seconds: 30);
      } catch (_) {
        // The channel implementation (e.g. HtmlWebSocketChannel on web)
        // doesn't expose a pingInterval – that's fine because browsers
        // usually keep connections alive themselves.
      }

      // Wait until the socket handshake either succeeds or fails so that
      // connection-level errors surface inside this try/catch.  The `ready`
      // Future is only available on certain channel implementations (e.g.
      // `HtmlWebSocketChannel` in web builds).  We therefore access it via
      // `dynamic` and swallow a `NoSuchMethodError` when the property is
      // missing so the code still compiles and runs everywhere.
      try {
        await (_channel! as dynamic).ready;
      } catch (_) {
        // Implementation does not expose a `ready` future – that's okay.
      }

      // If we get here the connection is established.
      _eventsController.add(ConnectedEvent());
      try {
        _sendRequest('client_functions', {
          'version': 1,
          'functions': [
            {
              'name': 'ui.set_status',
              'description': 'Override the inline status text (non-blocking)'
            }
          ]
        }, callId: _uuid.v4());
      } catch (_) {}
    } catch (e) {
      if (!_eventsController.isClosed) {
        final msg = e.toString().contains('Connection refused')
            ? 'Could not connect to server.'
            : e.toString();
        _eventsController.add(ErrorEvent(msg));
        _eventsController.add(DisconnectedEvent());
      }
      // Make sure we do not keep a broken channel reference.
      _channel = null;
      rethrow; // Bubble up so callers can react (they usually catch it).
    }

    // Start listening to incoming messages
    _channel!.stream.listen(_handleFrame, onDone: () {
      if (!_eventsController.isClosed) {
        _eventsController.add(DisconnectedEvent());
      }
      _channel = null;
      if (debug) debugPrint('${_tag()} [ws] Connection closed');
    }, onError: (error) {
      if (!_eventsController.isClosed) {
        _eventsController.add(ErrorEvent(error.toString()));
        _eventsController.add(DisconnectedEvent());
      }
      _channel = null;
    });
  }

  Future<RunIds> startRun(String prompt) async {
    if (debug) {
      debugPrint('${_tag()} startRun prompt="$prompt"'
          ' session=${_sessionId ?? 'none'}');
    }
    await connect();
    final callId = _uuid.v4();
    final completer = Completer<RunIds>();

    // Intermediate completer that will receive raw server payload.
    final intermediate = Completer<dynamic>();
    _pending[callId] = intermediate;

    intermediate.future.then((dynamic raw) {
      Map<String, dynamic>? value;
      if (raw is Map<String, dynamic>) {
        value = raw;
      } else if (raw is String) {
        try {
          final decoded = jsonDecode(raw);
          if (decoded is Map<String, dynamic>) value = decoded;
        } catch (_) {
          value = null;
        }
      }

      if (value != null) {
        final runId = value['run_id'] as String?;
        final sessionId = value['session_id'] as String?;
        if (debug) {
          debugPrint('${_tag()} startRun result run=$runId session=$sessionId');
        }
        if (sessionId != null) _sessionId = sessionId;
        completer.complete(RunIds(runId: runId, sessionId: sessionId));
      } else {
        completer.completeError('Unexpected response: $raw');
      }
    });

    final params = <String, dynamic>{
      'prompt': prompt,
    };
    if (_sessionId != null) params['session_id'] = _sessionId;
    if (_initialVariables != null && _initialVariables!.isNotEmpty) {
      params['variables'] = _initialVariables;
    }

    _sendRequest('start_run', params, callId: callId);

    return completer.future;
  }

  void setInitialVariables(Map<String, dynamic> vars) {
    _initialVariables = Map<String, dynamic>.from(vars);
  }

  Future<void> sendUserMessage(String runId, String content) {
    return _simpleCall('send_user_message', {
      'run_id': runId,
      'content': content,
    });
  }

  Future<void> stopRun(String runId) {
    return _simpleCall('stop_run', {'run_id': runId});
  }

  Future<void> toolApprovalResponse({
    required String approvalId,
    required bool approved,
    bool alwaysApprove = false,
    String? errorMessage,
  }) {
    return _simpleCall('tool_approval_response', {
      'approval_id': approvalId,
      'approved': approved,
      'always_approve': alwaysApprove,
      'error_message': errorMessage,
    });
  }

  Future<void> clientFunctions(int version, List<Map<String, dynamic>> functions) {
    return _simpleCall('client_functions', {
      'version': version,
      'functions': functions,
    });
  }

  Future<void> clientResponse(String requestId,
      {required bool ok,
      Map<String, dynamic>? result,
      Map<String, dynamic>? error}) {
    final params = {
      'request_id': requestId,
      'ok': ok,
    };
    if (result != null) params['result'] = result;
    if (error != null) params['error'] = error;
    return _simpleCall('client_response', params);
  }

  /// Fetch complete history for a given session id.
  /// Returns an empty list if the session does not exist or has no history.
  Future<List<dynamic>> getSessionHistory(String sessionId) async {
    try {
      await connect();
    } catch (_) {
      // Connection failed – return empty history.
      return <dynamic>[];
    }

    final callId = _uuid.v4();
    final completer = Completer<dynamic>();
    _pending[callId] = completer;
    _sendRequest('get_session_history', {'session_id': sessionId},
        callId: callId);

    final result = await completer.future;
    if (result is List) {
      return result;
    }
    if (result is String) {
      try {
        final decoded = jsonDecode(result);
        if (decoded is List) return decoded;
      } catch (_) {
        // Ignore parse errors
      }
    }
    return <dynamic>[];
  }

  /// Fetch list of sessions with archive state from the server.
  /// Returns an empty list if the request fails.
  Future<List<dynamic>> listSessions() async {
    try {
      await connect();
    } catch (_) {
      return <dynamic>[];
    }

    final callId = _uuid.v4();
    final completer = Completer<dynamic>();
    _pending[callId] = completer;
    _sendRequest('list_sessions', const {}, callId: callId);

    final result = await completer.future;
    if (result is List) {
      return result;
    }
    if (result is String) {
      try {
        final decoded = jsonDecode(result);
        if (decoded is List) return decoded;
      } catch (_) {
        // Ignore parse errors
      }
    }
    return <dynamic>[];
  }

  /// Mark [sessionId] as archived on the server.
  Future<void> archiveSession(String sessionId) {
    return _simpleCall('archive_session', {'session_id': sessionId});
  }

  /// List server-exposed functions for dynamic slash commands.
  Future<List<Map<String, dynamic>>> listServerFunctions() async {
    try {
      await connect();
    } catch (_) {
      return <Map<String, dynamic>>[];
    }

    final callId = _uuid.v4();
    final completer = Completer<dynamic>();
    _pending[callId] = completer;
    _sendRequest('list_server_functions', const {}, callId: callId);

    final result = await completer.future;
    List<Map<String, dynamic>> list = [];
    if (result is List) {
      for (final e in result) {
        if (e is Map) list.add(e.cast<String, dynamic>());
      }
    } else if (result is String) {
      try {
        final decoded = jsonDecode(result);
        if (decoded is List) {
          for (final e in decoded) {
            if (e is Map) list.add(e.cast<String, dynamic>());
          }
        }
      } catch (_) {}
    }
    return list;
  }

  /// Call an arbitrary server function.
  Future<Map<String, dynamic>> serverCall(String function,
      {Map<String, dynamic>? args}) async {
    await connect();
    final callId = _uuid.v4();
    final completer = Completer<dynamic>();
    _pending[callId] = completer;
    final params = <String, dynamic>{'function': function};
    if (args != null) params['args'] = args;
    if (_sessionId != null) params['session_id'] = _sessionId;
    _sendRequest('server_call', params, callId: callId);

    final result = await completer.future;
    if (result is Map<String, dynamic>) return result;
    if (result is String) {
      try {
        final decoded = jsonDecode(result);
        if (decoded is Map<String, dynamic>) return decoded;
      } catch (_) {}
    }
    return <String, dynamic>{};
  }

  /// Fetch deduplicated user input history across sessions.
  Future<List<String>> getUserHistory({int limit = 200, bool includeArchived = true}) async {
    try {
      await connect();
    } catch (_) {
      return <String>[];
    }
    final callId = _uuid.v4();
    final completer = Completer<dynamic>();
    _pending[callId] = completer;
    _sendRequest('get_user_history', {
      'limit': limit,
      'include_archived': includeArchived,
    }, callId: callId);
    final result = await completer.future;
    final out = <String>[];
    List list;
    if (result is List) {
      list = result;
    } else if (result is String) {
      try {
        final decoded = jsonDecode(result);
        if (decoded is List) {
          list = decoded;
        } else {
          return out;
        }
      } catch (_) {
        return out;
      }
    } else {
      return out;
    }
    for (final e in list) {
      if (e is Map) {
        final c = e['content']?.toString();
        if (c != null && c.trim().isNotEmpty) out.add(c.trim());
      } else if (e is String) {
        if (e.trim().isNotEmpty) out.add(e.trim());
      }
    }
    // Deduplicate preserving order
    final seen = <String>{};
    final dedup = <String>[];
    for (final s in out) {
      if (seen.add(s)) dedup.add(s);
    }
    return dedup;
  }

  // ---------------------------------------------------------------------------
  // Internals -----------------------------------------------------------------
  // ---------------------------------------------------------------------------
  Future<void> _simpleCall(String method, Map<String, dynamic> params) {
    final callId = _uuid.v4();
    final completer = Completer<dynamic>();
    _pending[callId] = completer;
    _sendRequest(method, params, callId: callId);
    return completer.future.then((_) {});
  }

  void _sendRequest(String method, Map<String, dynamic> args,
      {required String callId}) {
    final frame = {
      'jsonrpc': '2.0',
      'method': method,
      'params': args,
      'id': callId,
    };
    final payload = jsonEncode(frame);
    if (debug) debugPrint('${_tag()} → $payload');
    try {
      _channel?.sink.add(payload);
    } catch (e) {
      // Sending failed – most likely the socket wasn't open.
      if (!_eventsController.isClosed) {
        _eventsController.add(ErrorEvent(e.toString()));
        _eventsController.add(DisconnectedEvent());
      }
      final completer = _pending.remove(callId);
      completer?.completeError(e);
    }
  }

  void _handleFrame(dynamic data) {
    if (data is! String) return;

    Map<String, dynamic> msg;
    try {
      msg = jsonDecode(data) as Map<String, dynamic>;
    } catch (_) {
      return; // Not JSON → ignore
    }

    if (debug) debugPrint('${_tag()} ← $msg');

    // Handle notifications/requests first so UI updates even when a response is also present.
    if (msg.containsKey('method')) {
      final method = msg['method'] as String?;
      final args = (msg['params'] as Map?)?.cast<String, dynamic>() ?? {};
      final callId = msg['id'] as String?;
      if (method != null) {
        if (method == '_ping_' && callId != null) {
          // Respond immediately with pong
          _sendResponse(callId, 'pong');
        } else {
          _handleNotification(method, args);
          if (callId != null) _sendResponse(callId, true);
        }
      }
    }

    // Now handle response part (may be absent or null)
    if (msg.containsKey('id')) {
      final callId = msg['id'] as String?;
      if (callId != null) {
        final completer = _pending.remove(callId);
        if (completer != null) {
          if (msg.containsKey('error') && msg['error'] != null) {
            final err = msg['error'];
            if (err is Map) {
              final code = err['code'] is num ? (err['code'] as num).toInt() : null;
              final message = err['message']?.toString() ?? 'RPC error';
              completer.completeError(RemoteRpcError(code, message, err['data']));
            } else {
              completer.completeError(RemoteRpcError(null, err.toString()));
            }
          } else if (msg.containsKey('result')) {
            completer.complete(msg['result']);
          } else {
            completer.complete(null);
          }
        }
      }
    }
  }

  void _handleNotification(String method, Map<String, dynamic> payload) {
    switch (method) {
      case 'tool_called':
        _eventsController.add(ToolCalledEvent(
            payload['run_id']?.toString(),
            payload['call_id'] as String?,
            payload['tool'] as String? ?? '',
            payload['input'] as String? ?? ''));
        break;
      case 'tool_result':
        _eventsController.add(ToolResultEvent(
            payload['run_id']?.toString(),
            payload['call_id'] as String?,
            payload['tool'] as String? ?? '',
            payload['output'] as String? ?? '',
            (payload['metadata'] as Map?)?.cast<String, dynamic>()));
        break;
      case 'message_output':
        _eventsController.add(MessageOutputEvent(
            payload['run_id']?.toString(),
            payload['content'] as String,
            payload['is_final'] as bool? ?? false));
        break;
      case 'tool_approval_request':
        _eventsController.add(ToolApprovalRequestEvent(
          payload['approval_id'] as String,
          payload['tool'] as String,
          payload['arguments'] as String,
          metadata: (payload['metadata'] as Map?)?.cast<String, dynamic>(),
          runId: payload['run_id']?.toString(),
          sessionId: payload['session_id']?.toString(),
        ));
        break;
      case 'run_started':
        final rid = payload['run_id']?.toString() ?? '';
        final sid = payload['session_id']?.toString() ?? '';
        if (debug) {
          debugPrint('${_tag()} run_started run=$rid session=$sid');
        }
        _eventsController.add(RunStartedEvent(rid, sid));
        break;
      case 'run_end':
        UsageDetails? usage;
        final u = payload['usage'];
        if (u is Map) {
          usage = UsageDetails(
            requests: (u['requests'] as num?)?.toInt(),
            inputTokens: (u['input_tokens'] as num?)?.toInt(),
            outputTokens: (u['output_tokens'] as num?)?.toInt(),
            totalTokens: (u['total_tokens'] as num?)?.toInt(),
            inputTokensDetails: (u['input_tokens_details'] as Map?)
                ?.map((k, v) => MapEntry(k.toString(), (v as num).toInt())),
            outputTokensDetails: (u['output_tokens_details'] as Map?)
                ?.map((k, v) => MapEntry(k.toString(), (v as num).toInt())),
          );
        }
        RunEndError? err;
        final e = payload['error'];
        if (e is Map) {
          final msg = e['message']?.toString();
          final tp = e['type']?.toString();
          if (msg != null && tp != null) {
            err = RunEndError(message: msg, type: tp);
          }
        }
        _eventsController.add(RunEndEvent(
          payload['run_id']?.toString() ?? '',
          payload['end_reason']?.toString() ?? '',
          usage: usage,
          model: payload['model']?.toString(),
          contextWindow: (payload['context_window'] as num?)?.toInt(),
          maxOutputTokens: (payload['max_output_tokens'] as num?)?.toInt(),
          truncation: payload['truncation']?.toString(),
          error: err,
        ));
        break;
      case 'token':
        UsageDetails parseUsage(Map m) => UsageDetails(
              requests: (m['requests'] as num?)?.toInt(),
              inputTokens: (m['input_tokens'] as num?)?.toInt(),
              outputTokens: (m['output_tokens'] as num?)?.toInt(),
              totalTokens: (m['total_tokens'] as num?)?.toInt(),
              inputTokensDetails: (m['input_tokens_details'] as Map?)
                  ?.map((k, v) => MapEntry(k.toString(), (v as num).toInt())),
              outputTokensDetails: (m['output_tokens_details'] as Map?)
                  ?.map((k, v) => MapEntry(k.toString(), (v as num).toInt())),
            );
        UsageDetails delta = UsageDetails();
        UsageDetails totals = UsageDetails();
        final d = payload['delta'];
        final t = payload['totals'];
        if (d is Map) delta = parseUsage(d);
        if (t is Map) totals = parseUsage(t);
        _eventsController.add(TokenUsageEvent(
          runId: payload['run_id']?.toString() ?? '',
          turn: (payload['turn'] as num?)?.toInt() ?? 0,
          responseId: payload['response_id']?.toString() ?? '',
          model: payload['model']?.toString(),
          contextWindow: (payload['context_window'] as num?)?.toInt(),
          maxOutputTokens: (payload['max_output_tokens'] as num?)?.toInt(),
          truncation: payload['truncation']?.toString(),
          delta: delta,
          totals: totals,
        ));
        break;
      case 'run_status':
        _eventsController.add(RunStatusEvent(
          runId: payload['run_id']?.toString() ?? '',
          status: payload['status']?.toString() ?? '',
          message: payload['message']?.toString() ?? '',
          attempt: (payload['attempt'] as num?)?.toInt() ?? 0,
          total: (payload['total'] as num?)?.toInt() ?? 0,
          nextDelay: (payload['next_delay'] as num?)?.toDouble(),
        ));
        break;
      case 'client_request':
        _eventsController.add(ClientRequestEvent(
          requestId: payload['request_id']?.toString() ?? '',
          function: payload['function']?.toString() ?? '',
          args: (payload['args'] as Map?)?.cast<String, dynamic>() ?? const {},
          sessionId: payload['session_id']?.toString(),
          runId: payload['run_id']?.toString(),
          timeoutMs: (payload['timeout_ms'] as num?)?.toInt(),
          idempotencyKey: payload['idempotency_key']?.toString(),
        ));
        break;
      default:
        break;
    }
  }

  void _sendResponse(String callId, dynamic result) {
    final frame = {
      'jsonrpc': '2.0',
      'id': callId,
      'result': result,
    };
    final payload = jsonEncode(frame);
    if (debug) debugPrint('${_tag()} → $payload');
    try {
      _channel?.sink.add(payload);
    } catch (e) {
      if (!_eventsController.isClosed) {
        _eventsController.add(ErrorEvent(e.toString()));
        _eventsController.add(DisconnectedEvent());
      }
    }
  }

  String _tag() {
    if (label != null && label!.isNotEmpty) {
      return '[remote ${label!}]';
    }
    return '[remote]';
  }

  void dispose() {
    if (_disposed) return;
    _disposed = true;

    // Close the WebSocket first so the onDone callback fires (and can still
    // emit a DisconnectedEvent).  We ignore any errors if the socket is
    // already closed.
    _channel?.sink.close().catchError((_) {});
    _channel = null;

    // Give the onDone microtask a chance, then close the event controller.
    // This prevents "Bad state: Cannot add new events after calling close".
    Future.microtask(() {
      if (!_eventsController.isClosed) {
        // ignore: unawaited_futures
        _eventsController.close();
      }
    });
  }
}

/// Helper returned by startRun()
class RunIds {
  RunIds({required this.runId, required this.sessionId});
  final String? runId;
  final String? sessionId;
}

// extension removed; methods are now part of RemoteAgentClient
