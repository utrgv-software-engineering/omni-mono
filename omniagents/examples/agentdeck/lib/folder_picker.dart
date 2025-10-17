import 'package:flutter/material.dart';
import 'remote_agent_client.dart';

class FolderPickerDialog extends StatefulWidget {
  const FolderPickerDialog({super.key, required this.client, this.initialPath});
  final RemoteAgentClient client;
  final String? initialPath;

  @override
  State<FolderPickerDialog> createState() => _FolderPickerDialogState();
}

class _FolderPickerDialogState extends State<FolderPickerDialog> {
  String? _currentPath;
  String? _parentPath;
  List<Map<String, dynamic>> _entries = [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _init();
  }

  Future<void> _init() async {
    String? start = widget.initialPath;
    if (start == null || start.isEmpty) {
      try {
        final cwd = await widget.client.serverCall('fs_get_cwd');
        start = cwd['path']?.toString();
      } catch (_) {}
    }
    if (start == null || start.isEmpty) {
      try {
        final home = await widget.client.serverCall('fs_get_home');
        start = home['path']?.toString();
      } catch (_) {}
    }
    await _load(start);
  }

  Future<void> _load(String? path) async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final res = await widget.client.serverCall('fs_list_dir', args: {
        'path': path,
        'include_files': false,
        'ignore_hidden': true,
      });
      final entries = <Map<String, dynamic>>[];
      final list = res['entries'];
      if (list is List) {
        for (final e in list) {
          if (e is Map) entries.add(e.cast<String, dynamic>());
        }
      }
      setState(() {
        _currentPath = res['path']?.toString();
        _parentPath = res['parent']?.toString();
        _entries = entries;
        _loading = false;
      });
    } catch (e) {
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Choose Folder'),
      content: SizedBox(
        width: 480,
        height: 420,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? Center(child: Text(_error!))
                : Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Expanded(
                            child: Text(
                              _currentPath ?? '',
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                          IconButton(
                            icon: const Icon(Icons.arrow_upward),
                            tooltip: 'Up',
                            onPressed: _parentPath == null
                                ? null
                                : () => _load(_parentPath),
                          ),
                        ],
                      ),
                      const SizedBox(height: 8),
                      Expanded(
                        child: ListView.builder(
                          itemCount: _entries.length,
                          itemBuilder: (context, index) {
                            final e = _entries[index];
                            final isDir = e['is_dir'] == true;
                            final name = e['name']?.toString() ?? '';
                            return ListTile(
                              leading: Icon(isDir
                                  ? Icons.folder
                                  : Icons.insert_drive_file),
                              title: Text(name),
                              onTap: isDir
                                  ? () => _load(e['path']?.toString())
                                  : null,
                            );
                          },
                        ),
                      ),
                    ],
                  ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop<String?>(null),
          child: const Text('Cancel'),
        ),
        ElevatedButton(
          onPressed: _currentPath == null
              ? null
              : () => Navigator.of(context).pop<String?>(_currentPath),
          child: const Text('Use This Folder'),
        ),
      ],
    );
  }
}
