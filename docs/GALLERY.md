## 1-last-seen

Last Seen: an 11-year-old drone searches a park for a person from one photo, and a human confirms before it follows anyone.

## 2-opencv-detects

OpenCV 5's DNN engine runs a YOLO person detector on the drone's own video: 41 ms a frame, 24 fps, on a laptop CPU.

## 3-reid-match

Each person is compared with the photo two ways: the OpenCV model zoo re-ID network and the colours of their clothes.

## 4-is-this-them

Run 3 found me on its first scan at 0.85 and asked. The page is served by AWS Lambda; DynamoDB keeps the answer.

## 5-weak-match-no-question

Run 1, with a photo taken from the ground: I never scored above 0.24, so it never asked anyone. That is the hard case.

## 6-follow-and-map

After a human says that's them, it follows and drops a pin every second. Run 2: 90 s, 49 pins, lock held 47%.

## 7-camera-check

solvePnP on the printed markers in the flight footage puts the camera 5.9 m up where the barometer said 8.0 m.

## 8-safety-in-code

Safety lives in code, not the prompt: no follow without a confirmed person, and the envelope checks every control tick.
