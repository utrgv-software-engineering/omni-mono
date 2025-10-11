import 'dart:async';

import 'package:flutter/foundation.dart';

import 'models.dart';
import 'remote_agent_client.dart';

class ConversationController extends ChangeNotifier {
  ConversationController(this.client, {String? sessionId}) {
    if (sessionId != null && sessionId.isNotEmpty) {
      _sessionId = sessionId;
      client.resumeSession(_sessionId!);
    }
    _subscription = client.events.listen(_handleEvent);
  }

  final RemoteAgentClient client;
  late final StreamSubscription<RemoteEvent> _subscription;
  final StreamController<RemoteEvent> _eventStream =
      StreamController<RemoteEvent>.broadcast();
  final Set<String> _activeRunIds = <String>{};

  String? _sessionId;
  String? _currentRunId;
  bool _awaitingRunStart = false;

  Stream<RemoteEvent> get events => _eventStream.stream;
  String? get sessionId => _sessionId;
  String? get currentRunId => _currentRunId;

  Future<RunIds> startRun(String prompt) async {
    _awaitingRunStart = true;
    try {
      final ids = await client.startRun(prompt);
      if (ids.sessionId != null && ids.sessionId!.isNotEmpty) {
        _sessionId = ids.sessionId;
        client.resumeSession(_sessionId!);
        notifyListeners();
      }
      if (ids.runId != null && ids.runId!.isNotEmpty) {
        _activeRunIds.add(ids.runId!);
        _currentRunId = ids.runId;
      }
      return ids;
    } finally {
      _awaitingRunStart = false;
    }
  }

  Future<void> stopRun(String runId) {
    return client.stopRun(runId);
  }

  bool _shouldEmit(String? runId) {
    if (runId == null || runId.isEmpty) {
      return true;
    }
    if (_activeRunIds.contains(runId)) {
      return true;
    }
    if (_currentRunId != null && _currentRunId == runId) {
      return true;
    }
    return false;
  }

  void _handleEvent(RemoteEvent event) {
    if (event is RunStartedEvent) {
      final hasRunId = event.runId.isNotEmpty;
      final knownRun = hasRunId && _activeRunIds.contains(event.runId);
      if (!knownRun && !_awaitingRunStart) {
        return;
      }
      if (event.sessionId.isNotEmpty) {
        if (_sessionId == null ||
            _sessionId == event.sessionId) {
          _sessionId = event.sessionId;
          client.resumeSession(event.sessionId);
          notifyListeners();
        } else {
          return;
        }
      }
      if (hasRunId && !knownRun) {
        _activeRunIds.add(event.runId);
      }
      _awaitingRunStart = false;
      if (event.runId.isNotEmpty) {
        _currentRunId = event.runId;
      }
      _eventStream.add(event);
      return;
    }

    if (event is RunEndEvent) {
      if (_shouldEmit(event.runId)) {
        _eventStream.add(event);
        _activeRunIds.remove(event.runId);
        if (_currentRunId == event.runId) {
          _currentRunId = null;
        }
      }
      return;
    }

    if (event is MessageOutputEvent) {
      if (client.debug) {
        debugPrint('[controller ${client.label ?? '?'}] message run=${event.runId} emit=${_shouldEmit(event.runId)} active=$_activeRunIds current=$_currentRunId');
      }
      if (_shouldEmit(event.runId)) {
        _eventStream.add(event);
      }
      return;
    }

    if (event is ToolCalledEvent) {
      if (client.debug) {
        debugPrint('[controller ${client.label ?? '?'}] tool_called run=${event.runId} emit=${_shouldEmit(event.runId)}');
      }
      if (_shouldEmit(event.runId)) {
        _eventStream.add(event);
      }
      return;
    }

    if (event is ToolResultEvent) {
      if (client.debug) {
        debugPrint('[controller ${client.label ?? '?'}] tool_result run=${event.runId} emit=${_shouldEmit(event.runId)}');
      }
      if (_shouldEmit(event.runId)) {
        _eventStream.add(event);
      }
      return;
    }

    if (event is RunStatusEvent) {
      if (_shouldEmit(event.runId)) {
        _eventStream.add(event);
      }
      return;
    }

    if (event is TokenUsageEvent) {
      if (_shouldEmit(event.runId)) {
        _eventStream.add(event);
      }
      return;
    }

    _eventStream.add(event);
  }

  @override
  void dispose() {
    _subscription.cancel();
    _eventStream.close();
    super.dispose();
  }
}
