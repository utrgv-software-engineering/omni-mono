import 'package:flutter/material.dart';
import 'app_colors.dart';

/// A complete ColorScheme in keeping with TweetDeck's dark mode.
///
/// We build on [ColorScheme.dark] so that Material widgets choose sensible
/// foreground colours automatically.
final ColorScheme tweetDeckScheme = ColorScheme.dark(
  primary: tweetBlue,
  onPrimary: Colors.white,
  background: bgMain,
  onBackground: Colors.white70,
  surface: bgColumn,
  onSurface: Colors.white70,
  surfaceVariant: bgCardAlt,
  onSurfaceVariant: Colors.white70,
  secondary: textSubtle,
  onSecondary: Colors.white,
  error: errorRed,
  onError: Colors.white,
);
