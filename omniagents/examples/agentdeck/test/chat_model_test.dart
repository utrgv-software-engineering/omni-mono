import 'dart:async';

import 'package:flutter_test/flutter_test.dart';

import 'package:fleet_mgr/conversation_controller.dart';
import 'package:fleet_mgr/models.dart';
import 'package:fleet_mgr/remote_agent_client.dart';
import 'package:fleet_mgr/chat_screen.dart';

class FakeRemoteAgentClient extends RemoteAgentClient {
  FakeRemoteAgentClient() : super('ws://fake', debug: true);

  final StreamController<RemoteEvent> _events =
      StreamController<RemoteEvent>.broadcast();
  String? _sessionId;
  int _runCounter = 0;

  void emit(RemoteEvent event) {
    _events.add(event);
  }

  @override
  Stream<RemoteEvent> get events => _events.stream;

  @override
  void resumeSession(String sessionId) {
    _sessionId = sessionId;
  }

  @override
  Future<RunIds> startRun(String prompt) async {
    final runId = 'run_${_runCounter++}';
    final session = _sessionId ?? 'sess_${_runCounter}';
    _sessionId = session;
    Future.microtask(() {
      emit(RunStartedEvent(runId, session));
      emit(MessageOutputEvent(runId, 'reply:$prompt', false));
      emit(RunEndEvent(runId, 'completed'));
    });
    return RunIds(runId: runId, sessionId: session);
  }

  @override
  Future<void> stopRun(String runId) async {}

  @override
  Future<Map<String, dynamic>> serverCall(String function,
      {Map<String, dynamic>? args}) async {
    return {};
  }

  @override
  Future<void> clientResponse(String requestId,
      {required bool ok,
      Map<String, dynamic>? result,
      Map<String, dynamic>? error}) async {}

  @override
  Future<void> toolApprovalResponse(
      {required String approvalId,
      required bool approved,
      bool alwaysApprove = false,
      String? errorMessage}) async {}

  @override
  Future<List<String>> getUserHistory(
      {int limit = 200, bool includeArchived = true}) async {
    return <String>[];
  }

  @override
  Future<List<dynamic>> listSessions() async {
    return [];
  }

  @override
  Future<void> archiveSession(String sessionId) async {}

  @override
  Future<List<dynamic>> getSessionHistory(String sessionId) async {
    return [];
  }

  @override
  Future<List<Map<String, dynamic>>> listServerFunctions() async {
    return [];
  }
}

void main() {
  test('chat model receives events and clears thinking state', () async {
    final client = FakeRemoteAgentClient();
    final controller = ConversationController(client);
    final model = ChatModel(controller);

    model.send('hello column');

    await Future<void>.delayed(const Duration(milliseconds: 100));

    expect(model.items.length, 2);
    expect(model.items.last, isA<AssistantMessage>());
    expect(model.isStreaming, isFalse);
  });
}
