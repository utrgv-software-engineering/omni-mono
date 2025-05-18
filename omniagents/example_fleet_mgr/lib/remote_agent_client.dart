import 'dart:async';
import 'dart:convert';
// ignore: avoid_web_libraries_in_flutter (only used for standalone/mobile, web will use JS WebSocket)

import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:uuid/uuid.dart';
import 'package:flutter/foundation.dart';

import 'models.dart';

/// Lightweight client that speaks the wire-format expected by
/// `fastapi_websocket_rpc` (see RpcChannel in Python).  Frames look like:
///
///   {"request":  {"method":"start_run", "arguments":{..}, "call_id":"uuid"}}
///   {"response": {"call_id":"uuid", "result":{..}, "result_type":"dict"}}
///
/// This replaces the JSON-RPC 2.0 code we used earlier which the server did
/// not understand.
class RemoteAgentClient {
  RemoteAgentClient(this.endpoint, {this.token, this.debug = false});

  /// WebSocket URL of the OmniAgents server (for example ws://localhost:8000/ws).
  final String endpoint;

  /// Optional bearer-token – will be appended as ?token=X or &token=X.
  final String? token;

  final bool debug;

  WebSocketChannel? _channel;
  final _uuid = const Uuid();

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

    if (debug) debugPrint('[ws] Connecting to $url');
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
      if (debug) debugPrint('[ws] Connection closed');
    }, onError: (error) {
      if (!_eventsController.isClosed) {
        _eventsController.add(ErrorEvent(error.toString()));
        _eventsController.add(DisconnectedEvent());
      }
      _channel = null;
    });
  }

  Future<RunIds> startRun(String prompt) async {
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
        // Server may send Python-dict-as-string.  Try to coerce it into JSON.
        try {
          final jsonLike = raw.replaceAll("'", '"');
          value = jsonDecode(jsonLike) as Map<String, dynamic>?;
        } catch (_) {
          value = null;
        }
      }

      if (value != null) {
        final runId = value['run_id'] as String?;
        final sessionId = value['session_id'] as String?;
        if (sessionId != null) _sessionId = sessionId;
        completer.complete(RunIds(runId: runId, sessionId: sessionId));
      } else {
        completer.completeError('Unexpected response: $raw');
      }
    });

    _sendRequest(
        'start_run',
        {
          'prompt': prompt,
          'session_id': _sessionId,
        },
        callId: callId);

    return completer.future;
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
      'request': {
        'method': method,
        'arguments': args,
        'call_id': callId,
      }
    };
    final payload = jsonEncode(frame);
    if (debug) debugPrint('→ $payload');
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

    if (debug) debugPrint('← $msg');

    // Handle notifications first so UI updates even when a response is also present.
    if (msg.containsKey('request') && msg['request'] != null) {
      final req = msg['request'] as Map<String, dynamic>;
      final method = req['method'] as String?;
      final args = (req['arguments'] as Map?)?.cast<String, dynamic>() ?? {};
      final callId = req['call_id'] as String?;
      if (method != null) {
        if (method == '_ping_' && callId != null) {
          // Respond immediately with pong
          _sendResponse(callId, 'pong');
        } else {
          _handleNotification(method, args);
        }
      }
    }

    // Now handle response part (may be absent or null)
    if (msg.containsKey('response') && msg['response'] != null) {
      final resp = msg['response'] as Map<String, dynamic>;
      final callId = resp['call_id'] as String?;
      if (callId != null) {
        final completer = _pending.remove(callId);
        completer?.complete(resp['result']);
      }
    }
  }

  void _handleNotification(String method, Map<String, dynamic> payload) {
    switch (method) {
      case 'tool_called':
        _eventsController.add(ToolCalledEvent(
            payload['call_id'] as String?,
            payload['tool'] as String? ?? '',
            payload['input'] as String? ?? ''));
        break;
      case 'tool_result':
        _eventsController.add(ToolResultEvent(
            payload['call_id'] as String?,
            payload['tool'] as String? ?? '',
            payload['output'] as String? ?? ''));
        break;
      case 'message_output':
        _eventsController.add(MessageOutputEvent(payload['content'] as String,
            payload['is_final'] as bool? ?? false));
        break;
      case 'tool_approval_request':
        _eventsController.add(ToolApprovalRequestEvent(
          payload['approval_id'] as String,
          payload['tool'] as String,
          payload['arguments'] as String,
        ));
        break;
      case 'run_end':
        _eventsController.add(RunEndEvent(
            payload['run_id'] as String, payload['end_reason'] as String));
        break;
      default:
        break;
    }
  }

  void _sendResponse(String callId, dynamic result) {
    final frame = {
      'response': {
        'call_id': callId,
        'result': result,
        'result_type': result.runtimeType.toString(),
      }
    };
    final payload = jsonEncode(frame);
    if (debug) debugPrint('→ $payload');
    try {
      _channel?.sink.add(payload);
    } catch (e) {
      if (!_eventsController.isClosed) {
        _eventsController.add(ErrorEvent(e.toString()));
        _eventsController.add(DisconnectedEvent());
      }
    }
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
