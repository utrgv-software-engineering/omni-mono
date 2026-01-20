/// Model classes representing events coming from OmniAgents server.

sealed class RemoteEvent {}

class DisconnectedEvent extends RemoteEvent {}

class ErrorEvent extends RemoteEvent {
  ErrorEvent(this.message);
  final String message;
}

class ToolCalledEvent extends RemoteEvent {
  ToolCalledEvent(this.runId, this.callId, this.tool, this.input);
  final String? runId;
  final String? callId;
  final String tool;
  final String input;
}

class ToolResultEvent extends RemoteEvent {
  ToolResultEvent(
      this.runId, this.callId, this.tool, this.output, this.metadata);
  final String? runId;
  final String? callId;
  final String tool;
  final String output;
  final Map<String, dynamic>? metadata;
}

class MessageOutputEvent extends RemoteEvent {
  MessageOutputEvent(this.runId, this.content, this.isFinal);
  final String? runId;
  final String content;
  final bool isFinal;
}

class ToolApprovalRequestEvent extends RemoteEvent {
  ToolApprovalRequestEvent(
      {required this.requestId,
      required this.approvalId,
      required this.tool,
      required this.arguments,
      this.metadata,
      this.runId,
      this.sessionId});
  final String requestId;
  final String approvalId;
  final String tool;
  final String arguments;
  final Map<String, dynamic>? metadata;
  final String? runId;
  final String? sessionId;
}

class RunEndEvent extends RemoteEvent {
  RunEndEvent(this.runId, this.reason,
      {this.usage,
      this.model,
      this.maxInputTokens,
      this.maxOutputTokens,
      this.truncation,
      this.error});
  final String runId;
  final String reason;
  final UsageDetails? usage;
  final String? model;
  final int? maxInputTokens;
  final int? maxOutputTokens;
  final String? truncation;
  final RunEndError? error;
}

class ConnectedEvent extends RemoteEvent {}

class RunStartedEvent extends RemoteEvent {
  RunStartedEvent(this.runId, this.sessionId);
  final String runId;
  final String sessionId;
}

class UsageDetails {
  UsageDetails(
      {this.requests,
      this.inputTokens,
      this.outputTokens,
      this.totalTokens,
      this.inputTokensDetails,
      this.outputTokensDetails});
  final int? requests;
  final int? inputTokens;
  final int? outputTokens;
  final int? totalTokens;
  final Map<String, int>? inputTokensDetails;
  final Map<String, int>? outputTokensDetails;
}

class TokenUsageEvent extends RemoteEvent {
  TokenUsageEvent(
      {required this.runId,
      required this.turn,
      required this.responseId,
      this.model,
      this.maxInputTokens,
      this.maxOutputTokens,
      this.truncation,
      required this.delta,
      required this.totals});
  final String runId;
  final int turn;
  final String responseId;
  final String? model;
  final int? maxInputTokens;
  final int? maxOutputTokens;
  final String? truncation;
  final UsageDetails delta;
  final UsageDetails totals;
}

class RunStatusEvent extends RemoteEvent {
  RunStatusEvent(
      {required this.runId,
      required this.status,
      required this.message,
      required this.attempt,
      required this.total,
      this.nextDelay});
  final String runId;
  final String status;
  final String message;
  final int attempt;
  final int total;
  final double? nextDelay;
}

class ClientRequestEvent extends RemoteEvent {
  ClientRequestEvent(
      {required this.requestId,
      required this.function,
      required this.args,
      this.sessionId,
      this.runId,
      this.timeoutMs,
      this.idempotencyKey});
  final String requestId;
  final String function;
  final Map<String, dynamic> args;
  final String? sessionId;
  final String? runId;
  final int? timeoutMs;
  final String? idempotencyKey;
}

class RunEndError {
  RunEndError({required this.message, required this.type});
  final String message;
  final String type;
}
