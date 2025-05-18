/// Model classes representing events coming from OmniAgents server.

sealed class RemoteEvent {}

class DisconnectedEvent extends RemoteEvent {}

class ErrorEvent extends RemoteEvent {
  ErrorEvent(this.message);
  final String message;
}

class ToolCalledEvent extends RemoteEvent {
  ToolCalledEvent(this.callId, this.tool, this.input);
  final String? callId;
  final String tool;
  final String input;
}

class ToolResultEvent extends RemoteEvent {
  ToolResultEvent(this.callId, this.tool, this.output);
  final String? callId;
  final String tool;
  final String output;
}

class MessageOutputEvent extends RemoteEvent {
  MessageOutputEvent(this.content, this.isFinal);
  final String content;
  final bool isFinal;
}

class ToolApprovalRequestEvent extends RemoteEvent {
  ToolApprovalRequestEvent(this.approvalId, this.tool, this.arguments);
  final String approvalId;
  final String tool;
  final String arguments;
}

class RunEndEvent extends RemoteEvent {
  RunEndEvent(this.runId, this.reason);
  final String runId;
  final String reason;
}

class ConnectedEvent extends RemoteEvent {}
