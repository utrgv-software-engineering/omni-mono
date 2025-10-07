"""FastAPI routes for the React UI"""

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from typing import Optional, List, Dict, Any
from pathlib import Path
import json
import asyncio
from datetime import datetime
import csv
import io

from omniagents.core.paths import get_traces_dir

from .storage import SQLiteStorage

class StorageManager:
    """Manages multiple storage instances for different projects"""
    def __init__(self):
        self.storages: Dict[str, SQLiteStorage] = {}
        self.storage_dir = get_traces_dir()
    
    def get_storage(self, project: str) -> SQLiteStorage:
        """Get or create storage instance for a project"""
        if project not in self.storages:
            db_path = self.storage_dir / f"{project}.db"
            if not db_path.exists():
                raise ValueError(f"Project database not found: {project}")
            self.storages[project] = SQLiteStorage(str(db_path))
        return self.storages[project]
    
    def get_all_storages(self) -> Dict[str, SQLiteStorage]:
        """Load all available project storages"""
        if not self.storage_dir.exists():
            return {}
        
        for db_file in self.storage_dir.glob("*.db"):
            project = db_file.stem
            if project not in self.storages:
                self.storages[project] = SQLiteStorage(str(db_file))
        
        return self.storages

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass

manager = ConnectionManager()

def create_api_app(storage: Optional[SQLiteStorage] = None):
    app = FastAPI()
    
    # Create storage manager for handling multiple projects
    storage_manager = StorageManager()
    
    # If a specific storage was provided, use it as the default
    default_project = None
    if storage:
        # Extract project name from the db path
        db_path = Path(storage.db_path)
        default_project = db_path.stem
        storage_manager.storages[default_project] = storage
    
    # Add CORS middleware for development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    @app.get("/api/projects")
    async def get_projects():
        """Get list of all available project databases"""
        try:
            projects = SQLiteStorage.get_projects()
            return {"projects": projects}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.delete("/api/projects/{project}")
    async def delete_project(project: str):
        """Delete a project database"""
        try:
            # Validate project exists
            projects = SQLiteStorage.get_projects()
            if project not in projects:
                raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
            
            # Close any existing storage connections
            if project in storage_manager.storages:
                storage_manager.storages[project].conn.close()
                del storage_manager.storages[project]
            
            # Delete the database file
            db_path = storage_manager.storage_dir / f"{project}.db"
            if db_path.exists():
                db_path.unlink()

            # Also delete any journal files
            journal_path = storage_manager.storage_dir / f"{project}.db-journal"
            if journal_path.exists():
                journal_path.unlink()
            
            return {"success": True, "message": f"Project '{project}' deleted successfully"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/conversations")
    async def get_conversations(
        project: str = Query(..., description="Project name"),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        search: Optional[str] = None,
        has_errors: Optional[bool] = None,
        workflow_name: Optional[str] = None,
        dim: Optional[List[str]] = Query(None, description="Synthetic dimension filters as 'dimension:value'")
    ):
        """Get conversations (grouped traces) with filtering and pagination"""
        try:
            # Get the storage for this project
            try:
                storage = storage_manager.get_storage(project)
            except ValueError:
                return {"conversations": [], "total": 0}
            
            # Get many traces for coverage
            traces = storage.get_traces(limit=1000000)
            
            # Group by group_id
            conversations = {}
            for trace in traces:
                group_id = trace.get('group_id') or trace['trace_id']  # Use trace_id if no group
                
                if group_id not in conversations:
                    conversations[group_id] = {
                        'group_id': group_id,
                        'traces': [],
                        'trace_count': 0,
                        'has_errors': False,
                        'created_at': trace['created_at'],
                        'workflow_name': trace.get('workflow_name'),
                        'metadata': trace.get('metadata', {}),
                        'first_input': None  # Will extract from first trace
                    }
                
                conversations[group_id]['traces'].append(trace)
                conversations[group_id]['trace_count'] += 1
                
                # Check for errors in spans and extract first input from first trace
                spans = storage.get_spans_for_trace(trace['trace_id'])
                if any(s.get('error') for s in spans):
                    conversations[group_id]['has_errors'] = True
                
                # Extract first input from the first trace in the conversation
                if conversations[group_id]['first_input'] is None:
                    for span in spans:
                        try:
                            # Parse the data field
                            if isinstance(span.get('data'), str):
                                full_span = json.loads(span['data'])
                            else:
                                full_span = span.get('data', {})
                            
                            span_data = full_span.get('span_data', {})
                            
                            # Look for first user input in response or generation spans
                            if span.get('span_type') in ['response', 'generation'] and span_data.get('input'):
                                input_msgs = span_data['input']
                                user_msgs = [m for m in input_msgs if isinstance(m, dict) and m.get('role') == 'user']
                                if user_msgs:
                                    conversations[group_id]['first_input'] = user_msgs[0].get('content', '')
                                    break
                        except:
                            pass
            
            # Convert to list and sort by created_at
            conv_list = list(conversations.values())
            conv_list.sort(key=lambda x: x['created_at'], reverse=True)
            
            # Apply filters
            if search:
                conv_list = [c for c in conv_list 
                           if search.lower() in (c.get('workflow_name') or '').lower() 
                           or search.lower() in c['group_id'].lower()]
            
            if has_errors is not None:
                conv_list = [c for c in conv_list if c['has_errors'] == has_errors]

            if workflow_name:
                wn = workflow_name.lower()
                conv_list = [c for c in conv_list if (c.get('workflow_name') or '').lower() == wn]

            # Apply synthetic dimension filters
            if dim:
                dim_map: Dict[str, set] = {}
                for entry in dim:
                    try:
                        k, v = entry.split(":", 1)
                        dim_map.setdefault(k, set()).add(v)
                    except ValueError:
                        continue
                if dim_map:
                    filtered_conv = []
                    for conv in conv_list:
                        matched = False
                        for t in conv.get('traces', []):
                            md = t.get('metadata')
                            try:
                                if isinstance(md, str):
                                    meta = json.loads(md)
                                else:
                                    meta = md or {}
                            except:
                                meta = {}
                            mapping = meta.get('omniagents', {})\
                                               .get('synthetic', {})\
                                               .get('selection', {})\
                                               .get('mapping', {})
                            if all(mapping.get(k) in allowed for k, allowed in dim_map.items()):
                                matched = True
                                break
                        if matched:
                            filtered_conv.append(conv)
                    conv_list = filtered_conv
            
            # Apply pagination
            total = len(conv_list)
            paginated = conv_list[offset:offset + limit]
            
            # Remove full traces from response (just keep count)
            for conv in paginated:
                del conv['traces']
            
            return {
                "conversations": paginated,
                "total": total
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/synthetic_dimensions")
    async def get_synthetic_dimensions(
        project: str = Query(..., description="Project name"),
        workflow_name: Optional[str] = None
    ):
        """Get distinct synthetic dimension definitions for a project/workflow"""
        try:
            try:
                storage = storage_manager.get_storage(project)
            except ValueError:
                return {"dimensions": []}
            traces = storage.get_traces(limit=1000000)
            dims_map: Dict[str, Dict[str, Any]] = {}
            def display_name(s: str) -> str:
                return s.replace('_', ' ').title()
            for t in traces:
                if workflow_name and (t.get('workflow_name') or '').lower() != workflow_name.lower():
                    continue
                md = t.get('metadata')
                try:
                    if isinstance(md, str):
                        meta = json.loads(md)
                    else:
                        meta = md or {}
                except:
                    meta = {}
                dims = meta.get('omniagents', {})\
                           .get('synthetic', {})\
                           .get('catalog', {})\
                           .get('dimensions', [])
                for d in dims or []:
                    name = d.get('name')
                    if not name:
                        continue
                    label = d.get('label') or display_name(name)
                    if name not in dims_map:
                        dims_map[name] = { 'label': label, 'values': {} }
                    for v in d.get('values', []) or []:
                        vname = v.get('name')
                        if not vname:
                            continue
                        vlabel = v.get('label') or display_name(vname)
                        dims_map[name]['values'][vname] = vlabel
            dimensions = []
            for name, defn in dims_map.items():
                values = [{ 'name': vn, 'label': vl } for vn, vl in sorted(defn['values'].items())]
                dimensions.append({ 'name': name, 'label': defn['label'], 'values': values })
            dimensions.sort(key=lambda d: d['label'].lower())
            return { 'dimensions': dimensions }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/workflows")
    async def get_workflows(project: str = Query(..., description="Project name")):
        """Get distinct workflow names for a project"""
        try:
            try:
                storage = storage_manager.get_storage(project)
            except ValueError:
                return {"workflows": []}
            names = storage.get_workflow_names()
            return {"workflows": names}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/conversations/{project}/{group_id}/traces")
    async def get_conversation_traces(
        project: str,
        group_id: str
    ):
        """Get all traces in a conversation"""
        try:
            storage = storage_manager.get_storage(project)
            
            # Get all traces for this group_id
            all_traces = storage.get_traces(limit=10000)
            
            # Filter by group_id (or traces where trace_id == group_id for single traces)
            # Make sure we have mutable dicts
            conversation_traces = []
            for t in all_traces:
                if t.get('group_id') == group_id or (not t.get('group_id') and t['trace_id'] == group_id):
                    # Create a new dict to ensure mutability
                    trace_dict = dict(t)
                    conversation_traces.append(trace_dict)
            
            # Sort by created_at
            conversation_traces.sort(key=lambda x: x['created_at'])
            
            # Add span counts and error flags, extract input/output
            for trace in conversation_traces:
                spans = storage.get_spans_for_trace(trace['trace_id'])
                trace['span_count'] = len(spans)
                trace['has_errors'] = any(s.get('error') for s in spans)
                
                # Ensure metadata exists and is a dict
                if 'metadata' not in trace:
                    trace['metadata'] = {}
                elif trace['metadata'] is None:
                    trace['metadata'] = {}
                elif isinstance(trace['metadata'], str):
                    try:
                        trace['metadata'] = json.loads(trace['metadata'])
                        if trace['metadata'] is None:
                            trace['metadata'] = {}
                    except:
                        trace['metadata'] = {}
                
                # Try to extract input/output from spans
                # Sort spans by sequence number to get chronological order
                sorted_spans = sorted(spans, key=lambda x: x.get('sequence_number', 0))
                
                trace_input = None
                trace_outputs = []
                previous_messages = []  # Track messages from previous traces
                
                for span in sorted_spans:
                    try:
                        # Parse the data field which contains the full span JSON
                        if isinstance(span.get('data'), str):
                            full_span = json.loads(span['data'])
                        else:
                            full_span = span.get('data', {})
                        
                        span_data = full_span.get('span_data', {})
                        
                        # Extract input from response or generation spans - find the NEW user input for THIS trace
                        if span.get('span_type') in ['response', 'generation'] and span_data.get('input') and not trace_input:
                            input_msgs = span_data['input']
                            
                            # Look through the input messages to find the most recent user message
                            # that isn't from a previous trace
                            user_msgs = []
                            for msg in input_msgs:
                                if isinstance(msg, dict) and msg.get('role') == 'user':
                                    user_msgs.append(msg.get('content', ''))
                            
                            # For the first trace, use the first user message
                            # For subsequent traces, use the last user message (most recent)
                            if user_msgs:
                                # Check if this is likely a continuation (has multiple user messages)
                                if len(user_msgs) > 1:
                                    # This is a continuation - use the last (most recent) user message
                                    trace_input = user_msgs[-1]
                                else:
                                    # This is the first interaction - use the first message
                                    trace_input = user_msgs[0]
                        
                        # Collect ALL response outputs (we'll use the last one)
                        if span.get('span_type') == 'response' and span_data.get('response'):
                            response = span_data['response']
                            if response.get('output') and isinstance(response['output'], list):
                                for output_item in response['output']:
                                    # Handle message type outputs (actual text responses)
                                    if output_item.get('type') == 'message' and output_item.get('content'):
                                        for content_item in output_item['content']:
                                            if content_item.get('type') == 'output_text':
                                                text = content_item.get('text', '')
                                                if text:
                                                    trace_outputs.append(text)
                                    # Handle function calls (show what function was called)
                                    elif output_item.get('type') == 'function_call':
                                        func_name = output_item.get('name', 'function')
                                        trace_outputs.append(f"→ {func_name}()")
                        
                        # Handle Chat Completions format (generation spans)
                        if span.get('span_type') == 'generation' and span_data.get('output'):
                            output_list = span_data['output']
                            if isinstance(output_list, list) and output_list:
                                # Chat Completions has nested structure: output[0].output[0].content[0].text
                                first_output = output_list[0]
                                if isinstance(first_output, dict) and 'output' in first_output:
                                    inner_outputs = first_output['output']
                                    if isinstance(inner_outputs, list):
                                        for item in inner_outputs:
                                            if isinstance(item, dict):
                                                # Check for message with content
                                                if item.get('type') == 'message' and item.get('role') == 'assistant':
                                                    content_items = item.get('content', [])
                                                    if isinstance(content_items, list):
                                                        for content in content_items:
                                                            if isinstance(content, dict) and content.get('text'):
                                                                trace_outputs.append(content['text'])
                                                # Check for function call
                                                elif item.get('type') == 'function_call':
                                                    func_name = item.get('name', 'function')
                                                    trace_outputs.append(f"→ {func_name}()")
                    except Exception as e:
                        pass
                
                # Use the LAST output as the trace output (final response)
                trace_output = trace_outputs[-1] if trace_outputs else None
                
                # Set the extracted input/output in metadata
                if trace_input:
                    trace['metadata']['input'] = trace_input
                if trace_output:
                    trace['metadata']['output'] = trace_output
                
                # Calculate duration from spans
                if spans:
                    started_times = [s['started_at'] for s in spans if s.get('started_at')]
                    ended_times = [s['ended_at'] for s in spans if s.get('ended_at')]
                    
                    if started_times and ended_times:
                        start = min(datetime.fromisoformat(t) for t in started_times)
                        end = max(datetime.fromisoformat(t) for t in ended_times)
                        trace['duration'] = (end - start).total_seconds() * 1000
                    else:
                        trace['duration'] = 0
                else:
                    trace['duration'] = 0
            
            return {
                "traces": conversation_traces,
                "group_id": group_id,
                "total": len(conversation_traces)
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/traces")
    async def get_traces(
        project: str = Query(..., description="Project name"),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        search: Optional[str] = None,
        has_errors: Optional[bool] = None,
        span_type: Optional[str] = None
    ):
        """Get traces with filtering and pagination"""
        try:
            # Get the storage for this project
            try:
                storage = storage_manager.get_storage(project)
            except ValueError:
                # Project doesn't exist
                return {"traces": [], "total": 0}
            
            # Get traces from the storage
            traces = storage.get_traces(limit=limit + offset)
            
            # Apply filters
            filtered_traces = traces
            
            if search:
                filtered_traces = [
                    t for t in filtered_traces 
                    if search.lower() in (t.get('workflow_name') or '').lower()
                ]
            
            if has_errors is not None:
                # Check if trace has any spans with errors
                traces_with_errors = set()
                for trace in filtered_traces:
                    spans = storage.get_spans_for_trace(trace['trace_id'])
                    if any(s.get('error') for s in spans):
                        traces_with_errors.add(trace['trace_id'])
                
                if has_errors:
                    filtered_traces = [t for t in filtered_traces if t['trace_id'] in traces_with_errors]
                else:
                    filtered_traces = [t for t in filtered_traces if t['trace_id'] not in traces_with_errors]
            
            if span_type:
                # Filter traces that contain specific span type
                traces_with_type = set()
                for trace in filtered_traces:
                    spans = storage.get_spans_for_trace(trace['trace_id'])
                    if any(s.get('span_type') == span_type for s in spans):
                        traces_with_type.add(trace['trace_id'])
                filtered_traces = [t for t in filtered_traces if t['trace_id'] in traces_with_type]
            
            # Apply pagination
            paginated = filtered_traces[offset:offset + limit]
            
            return {
                "traces": paginated,
                "total": len(filtered_traces)
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/traces/{project}/{trace_id}")
    async def get_trace(project: str, trace_id: str):
        """Get a single trace with all its spans"""
        try:
            # Get the storage for this project
            try:
                storage = storage_manager.get_storage(project)
            except ValueError:
                raise HTTPException(status_code=404, detail="Project not found")
            
            # Get the specific trace by ID
            trace = storage.get_trace(trace_id)
            
            if not trace:
                raise HTTPException(status_code=404, detail="Trace not found")
            
            spans = storage.get_spans_for_trace(trace_id)
            
            # Parse span data and calculate durations
            processed_spans = []
            for span in spans:
                span_data = json.loads(span.get('data', '{}'))
                processed_span = {
                    **span,
                    'data': span_data
                }
                
                # Calculate duration if both timestamps exist
                if span.get('started_at') and span.get('ended_at'):
                    start = datetime.fromisoformat(span['started_at'])
                    end = datetime.fromisoformat(span['ended_at'])
                    processed_span['duration'] = (end - start).total_seconds() * 1000
                
                processed_spans.append(processed_span)
            
            # Build span hierarchy
            span_map = {s['span_id']: s for s in processed_spans}
            for span in processed_spans:
                if span.get('parent_id') and span['parent_id'] in span_map:
                    parent = span_map[span['parent_id']]
                    if 'children' not in parent:
                        parent['children'] = []
                    parent['children'].append(span)
            
            return {
                "trace": trace,
                "spans": processed_spans
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/search")
    async def search(
        project: str = Query(..., description="Project name"),
        q: str = Query(..., min_length=1),
        type: str = Query("traces", regex="^(traces|spans)$")
    ):
        """Search traces or spans within a project"""
        try:
            # Get the storage for this project
            try:
                storage = storage_manager.get_storage(project)
            except ValueError:
                return {"results": [], "total": 0}
            
            results = []
            
            if type == "traces":
                traces = storage.get_traces(limit=1000)
                results = [
                    t for t in traces
                    if q.lower() in (t.get('workflow_name') or '').lower() or
                       q.lower() in t.get('trace_id', '').lower()
                ]
            else:
                # Search in spans
                traces = storage.get_traces(limit=1000)
                for trace in traces:
                    spans = storage.get_spans_for_trace(trace['trace_id'])
                    for span in spans:
                        span_data = json.loads(span.get('data', '{}'))
                        if (q.lower() in span.get('span_name', '').lower() or
                            q.lower() in span.get('span_type', '').lower() or
                            q.lower() in json.dumps(span_data).lower()):
                            results.append(span)
            
            return {
                "results": results[:100],  # Limit results
                "total": len(results)
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/stats/overview")
    async def get_stats(project: str = Query(..., description="Project name")):
        """Get statistics overview for a project"""
        try:
            # Get the storage for this project
            try:
                storage = storage_manager.get_storage(project)
            except ValueError:
                return {
                    "total_traces": 0,
                    "total_spans": 0,
                    "error_rate": 0,
                    "avg_duration": 0,
                    "spans_by_type": {}
                }
            
            traces = storage.get_traces(limit=10000)
            total_spans = 0
            error_count = 0
            durations = []
            spans_by_type = {}
            
            for trace in traces:
                spans = storage.get_spans_for_trace(trace['trace_id'])
                total_spans += len(spans)
                
                for span in spans:
                    # Count errors
                    if span.get('error'):
                        error_count += 1
                    
                    # Count by type
                    span_type = span.get('span_type', 'unknown')
                    spans_by_type[span_type] = spans_by_type.get(span_type, 0) + 1
                    
                    # Calculate duration
                    if span.get('started_at') and span.get('ended_at'):
                        start = datetime.fromisoformat(span['started_at'])
                        end = datetime.fromisoformat(span['ended_at'])
                        duration = (end - start).total_seconds() * 1000
                        durations.append(duration)
            
            avg_duration = sum(durations) / len(durations) if durations else 0
            error_rate = (error_count / total_spans * 100) if total_spans > 0 else 0
            
            return {
                "total_traces": len(traces),
                "total_spans": total_spans,
                "error_rate": error_rate,
                "avg_duration": avg_duration,
                "spans_by_type": spans_by_type
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/analysis/{project}")
    async def get_all_analysis(project: str, workflow_name: Optional[str] = None):
        """Get all analysis data for a project, optionally filtered by workflow name"""
        try:
            storage = storage_manager.get_storage(project)
            analysis_data = storage.get_all_analysis(project)
            
            # If a workflow filter is provided, determine which group_ids belong to it
            if workflow_name:
                traces = storage.get_traces(limit=10000)
                wn = workflow_name.lower()
                allowed_group_ids = set()
                for t in traces:
                    twn = (t.get('workflow_name') or '').lower()
                    if twn == wn:
                        gid = t.get('group_id') or t.get('trace_id')
                        if gid:
                            allowed_group_ids.add(gid)
                # Filter analysis to only allowed group_ids
                analysis_data = [a for a in analysis_data if a.get('group_id') in allowed_group_ids]
            
            # Convert to frontend format
            result = {}
            for item in analysis_data:
                result[item['group_id']] = {
                    'markedForAnalysis': bool(item['marked_for_analysis']),
                    'judgment': item['judgment'],
                    'notes': item['notes'] or '',
                    'analyzedAt': item['analyzed_at']
                }
            
            return result
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/analysis/{project}/{group_id}")
    async def get_analysis(project: str, group_id: str):
        """Get analysis data for a specific conversation"""
        try:
            storage = storage_manager.get_storage(project)
            analysis = storage.get_analysis(project, group_id)
            
            if not analysis:
                return {
                    'markedForAnalysis': False,
                    'judgment': None,
                    'notes': '',
                    'analyzedAt': None
                }
            
            return {
                'markedForAnalysis': bool(analysis['marked_for_analysis']),
                'judgment': analysis['judgment'],
                'notes': analysis['notes'] or '',
                'analyzedAt': analysis['analyzed_at']
            }
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.put("/api/analysis/{project}/{group_id}")
    async def update_analysis(
        project: str,
        group_id: str,
        analysis_data: Dict[str, Any]
    ):
        """Update or create analysis data for a conversation"""
        try:
            storage = storage_manager.get_storage(project)
            storage.upsert_analysis(project, group_id, analysis_data)
            
            # Return the updated data
            return {
                'markedForAnalysis': analysis_data.get('markedForAnalysis', False),
                'judgment': analysis_data.get('judgment'),
                'notes': analysis_data.get('notes', ''),
                'analyzedAt': analysis_data.get('analyzedAt')
            }
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.delete("/api/analysis/{project}/{group_id}")
    async def delete_analysis(project: str, group_id: str):
        """Delete analysis data for a conversation"""
        try:
            storage = storage_manager.get_storage(project)
            storage.delete_analysis(group_id)
            return {"message": "Analysis deleted successfully"}
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/categories/{project}")
    async def get_failure_categories(project: str):
        """Get all failure categories for a project"""
        try:
            storage = storage_manager.get_storage(project)
            categories = storage.get_failure_categories(project)
            
            # Get usage stats
            usage_stats = storage.get_category_usage_stats(project)
            
            # Add usage count to each category
            for category in categories:
                category['usageCount'] = usage_stats.get(category['id'], 0)
            
            return categories
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.post("/api/categories/{project}")
    async def create_failure_category(
        project: str,
        category_data: Dict[str, Any]
    ):
        """Create a new failure category"""
        try:
            storage = storage_manager.get_storage(project)
            
            # Validate required fields
            if 'name' not in category_data or not category_data['name'].strip():
                raise HTTPException(status_code=400, detail="Category name is required")
            
            # Create the category
            category_id = storage.create_failure_category(
                project,
                category_data['name'].strip(),
                category_data.get('description', '').strip() or None
            )
            
            return {
                'id': category_id,
                'name': category_data['name'].strip(),
                'description': category_data.get('description', '').strip() or None,
                'usageCount': 0
            }
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                raise HTTPException(status_code=409, detail="Category with this name already exists")
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.delete("/api/categories/{project}/{category_id}")
    async def delete_failure_category(project: str, category_id: int):
        """Delete a failure category"""
        try:
            storage = storage_manager.get_storage(project)
            
            # Get usage count before deletion
            usage_count = storage.get_category_usage(project, category_id)
            
            # Delete the category
            deleted = storage.delete_failure_category(project, category_id)
            
            if not deleted:
                raise HTTPException(status_code=404, detail="Category not found")
            
            return {
                'message': 'Category deleted successfully',
                'previousUsageCount': usage_count
            }
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/analysis/{project}/{group_id}/categories")
    async def get_analysis_categories(project: str, group_id: str):
        """Get categories for a specific analysis"""
        try:
            storage = storage_manager.get_storage(project)
            category_ids = storage.get_analysis_categories(group_id)
            return {'categoryIds': category_ids}
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.put("/api/analysis/{project}/{group_id}/categories")
    async def update_analysis_categories(
        project: str,
        group_id: str,
        data: Dict[str, Any]
    ):
        """Update categories for an analysis"""
        try:
            storage = storage_manager.get_storage(project)
            category_ids = data.get('categoryIds', [])
            
            # Validate that all category IDs are integers
            try:
                category_ids = [int(cat_id) for cat_id in category_ids]
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="Invalid category IDs")
            
            storage.set_analysis_categories(project, group_id, category_ids)
            return {'categoryIds': category_ids}
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/export/{project}/{trace_id}")
    async def export_trace(
        project: str,
        trace_id: str,
        format: str = Query("json", regex="^(json|csv)$")
    ):
        """Export a trace in different formats"""
        try:
            # Get the storage for this project
            try:
                storage = storage_manager.get_storage(project)
            except ValueError:
                raise HTTPException(status_code=404, detail="Project not found")
            
            # Get the specific trace by ID
            trace = storage.get_trace(trace_id)
            
            if not trace:
                raise HTTPException(status_code=404, detail="Trace not found")
            
            spans = storage.get_spans_for_trace(trace_id)
            
            if format == "json":
                data = {
                    "trace": trace,
                    "spans": spans
                }
                return StreamingResponse(
                    io.BytesIO(json.dumps(data, indent=2).encode()),
                    media_type="application/json",
                    headers={"Content-Disposition": f"attachment; filename=trace_{trace_id}.json"}
                )
            else:
                # CSV format
                output = io.StringIO()
                writer = csv.DictWriter(output, fieldnames=[
                    'span_id', 'span_type', 'span_name', 'started_at', 'ended_at', 
                    'parent_id', 'sequence_number', 'error'
                ])
                writer.writeheader()
                
                for span in spans:
                    writer.writerow({
                        'span_id': span.get('span_id'),
                        'span_type': span.get('span_type'),
                        'span_name': span.get('span_name'),
                        'started_at': span.get('started_at'),
                        'ended_at': span.get('ended_at'),
                        'parent_id': span.get('parent_id'),
                        'sequence_number': span.get('sequence_number'),
                        'error': 'Yes' if span.get('error') else 'No'
                    })
                
                return StreamingResponse(
                    io.BytesIO(output.getvalue().encode()),
                    media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=trace_{trace_id}.csv"}
                )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.websocket("/api/ws/traces")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket for real-time trace updates"""
        await manager.connect(websocket)
        try:
            while True:
                # Keep connection alive
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            manager.disconnect(websocket)
    
    # Serve static files in production
    # Prefer packaged assets under studio/tracing/static; fall back to repo root for dev
    candidate_dirs = [
        Path(__file__).parent / "static",
        Path(__file__).parent.parent / "static",
        Path(__file__).parent.parent / "web" / "dist",
    ]
    for static_dir in candidate_dirs:
        if static_dir.exists():
            app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
            break
    
    return app, manager
