import 'package:flutter/material.dart';
import 'theme/app_colors.dart';
import 'theme/tweetdeck_scheme.dart';
import 'package:provider/provider.dart';
import 'agents_repository.dart';
import 'multi_column_screen.dart';

void main() {
  runApp(const MainApp());
}

class MainApp extends StatelessWidget {
  const MainApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider(create: (_) => AgentsRepository()),
      ],
      child: MaterialApp(
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
          useMaterial3: true,
          colorScheme: tweetDeckScheme,
          scaffoldBackgroundColor: tweetDeckScheme.background,
          appBarTheme: const AppBarTheme(
            backgroundColor: bgColumn,
            foregroundColor: Colors.white,
            elevation: 0,
          ),
          textButtonTheme: TextButtonThemeData(
            style: TextButton.styleFrom(foregroundColor: tweetBlue),
          ),
          elevatedButtonTheme: ElevatedButtonThemeData(
            style: ElevatedButton.styleFrom(
              backgroundColor: tweetBlue,
              foregroundColor: Colors.white,
            ),
          ),
        ),
        home: const MultiColumnScreen(),
      ),
    );
  }
}
