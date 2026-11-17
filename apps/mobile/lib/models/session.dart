class CoreCredentials {
  const CoreCredentials({required this.baseUrl, required this.token});

  final String baseUrl;
  final String token;
}

class SessionRecord {
  const SessionRecord({
    required this.sessionId,
    required this.status,
    required this.exercise,
    required this.view,
    required this.targetReps,
    this.createdAt,
    this.startedAt,
    this.endedAt,
    this.reportUrl,
    this.liveSummary,
  });

  final String sessionId;
  final String status;
  final String exercise;
  final String view;
  final int targetReps;
  final String? createdAt;
  final String? startedAt;
  final String? endedAt;
  final String? reportUrl;
  final Map<String, dynamic>? liveSummary;

  bool get isActive =>
      const {'created', 'preparing', 'active', 'paused'}.contains(status);

  factory SessionRecord.fromJson(Map<String, dynamic> json) {
    return SessionRecord(
      sessionId: json['session_id'] as String,
      status: json['status'] as String,
      exercise: json['exercise'] as String,
      view: json['view'] as String,
      targetReps: (json['target_reps'] as num).toInt(),
      createdAt: json['created_at'] as String?,
      startedAt: json['started_at'] as String?,
      endedAt: json['ended_at'] as String?,
      reportUrl: json['report_url'] as String?,
      liveSummary: json['live_summary'] as Map<String, dynamic>?,
    );
  }

  SessionRecord copyWith({String? status}) {
    return SessionRecord(
      sessionId: sessionId,
      status: status ?? this.status,
      exercise: exercise,
      view: view,
      targetReps: targetReps,
      createdAt: createdAt,
      startedAt: startedAt,
      endedAt: endedAt,
      reportUrl: reportUrl,
      liveSummary: liveSummary,
    );
  }
}

class LiveMetrics {
  const LiveMetrics({
    this.exercise = 'squat',
    this.phase = 'unknown',
    this.reps = 0,
    this.confidence = 0,
    this.fps = 0,
    this.paused = false,
    this.jointAngle,
    this.elapsedSeconds = 0,
  });

  final String exercise;
  final String phase;
  final int reps;
  final double confidence;
  final double fps;
  final bool paused;
  final double? jointAngle;
  final double elapsedSeconds;

  factory LiveMetrics.fromJson(Map<String, dynamic> json) {
    return LiveMetrics(
      exercise: json['exercise'] as String? ?? 'squat',
      phase: json['phase'] as String? ?? 'unknown',
      reps: (json['reps'] as num?)?.toInt() ?? 0,
      confidence: (json['confidence'] as num?)?.toDouble() ?? 0,
      fps: (json['fps'] as num?)?.toDouble() ?? 0,
      paused: json['paused'] as bool? ?? false,
      jointAngle: (json['joint_angle'] as num?)?.toDouble(),
      elapsedSeconds: (json['elapsed_s'] as num?)?.toDouble() ?? 0,
    );
  }
}
