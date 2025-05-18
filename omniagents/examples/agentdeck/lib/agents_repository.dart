import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Describes one remote AI agent endpoint the user configured.
class AgentConfig {
  AgentConfig({required this.name, required this.endpoint, this.token});

  final String name;
  final String endpoint;
  final String? token;

  Map<String, dynamic> toJson() => {
        'name': name,
        'endpoint': endpoint,
        'token': token,
      };

  factory AgentConfig.fromJson(Map<String, dynamic> json) => AgentConfig(
        name: json['name'] as String? ?? 'Agent',
        endpoint: json['endpoint'] as String? ?? '',
        token: json['token'] as String?,
      );
}

/// Holds the list of agents and persists them using `SharedPreferences`.
class AgentsRepository extends ChangeNotifier {
  static const _prefsKey = 'agents_list_v1';

  AgentsRepository() {
    _load();
  }

  final List<AgentConfig> _agents = [];
  List<AgentConfig> get agents => List.unmodifiable(_agents);

  Future<void> addAgent(AgentConfig agent) async {
    _agents.add(agent);
    await _save();
    notifyListeners();
  }

  Future<void> removeAgent(AgentConfig agent) async {
    _agents.remove(agent);
    await _save();
    notifyListeners();
  }

  Future<void> updateAgent(
      AgentConfig oldAgent, AgentConfig updatedAgent) async {
    final index = _agents.indexOf(oldAgent);
    if (index == -1) return;
    _agents[index] = updatedAgent;
    await _save();
    notifyListeners();
  }

  // -------------------------------------------------------------------------
  // Persistence -------------------------------------------------------------
  // -------------------------------------------------------------------------

  Future<void> _load() async {
    final prefs = await SharedPreferences.getInstance();
    final strings = prefs.getStringList(_prefsKey) ?? [];
    _agents
      ..clear()
      ..addAll(strings.map(
          (s) => AgentConfig.fromJson(jsonDecode(s) as Map<String, dynamic>)));
    notifyListeners();
  }

  Future<void> _save() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setStringList(
        _prefsKey, _agents.map((a) => jsonEncode(a.toJson())).toList());
  }
}
