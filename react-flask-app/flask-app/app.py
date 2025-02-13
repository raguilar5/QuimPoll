from utils import recognize_gesture, annotate_gesture_and_hand_landmark, gesture_crop_dimensions
from pathlib import Path
from ultralytics import YOLO
from flask import Flask, Response, request, send_from_directory, send_file
from flask_cors import CORS, cross_origin
from base64 import b64encode
from PIL import Image
import os
import re
import io
import json
import argparse
import cv2

GESTURE_THRESHOLD = 0.6

model = YOLO(r"models/best.pt")
app = Flask(__name__, static_url_path="/outputs")
CORS(app, support_credentials=True)
image_extensions = ["png", "jpg", "webp"]
video_extensions = ["mp4", "webm"]


@app.route("/inference", methods=["PUT", "GET"])
@cross_origin(supports_credentials=True)
def inference():
    if request.method == "PUT":
        poll_data = {
            "people_detected": {
                "count": 0,
                "media": "",
            },
            "gestures_detected": {"count": 0, "media": [], "gestures": {}},
        }

        [
            f.unlink() for f in Path("outputs").glob("*") if f.is_file()
        ]  # clear outputs folder for each new inference request
        if request.files["file"]:
            file = request.files["file"]
            file_extension = file.filename.rsplit(".", 1)[1].lower()

            #   Process image file
            if file_extension in image_extensions:
                file.save(f"media.{file_extension}")
                image = cv2.imread(f"media.{file_extension}")  # returns a BGR image
                results = model.predict(
                    image, save=False
                )  # results is an array, each item corresponds to each file passed in or each frame of a video
                pose_annotations = results[0].plot()  # .plot() returns an annotated image
                height, width = image.shape[:2]
                poll_data["people_detected"]["media"] = pose_annotations

                # each loop represents one person detected (i.e. three people detected, three loops)
                for i, keypoints in enumerate(
                    results[0].keypoints.cpu().numpy()
                ):  # .keypoints returns a Tensor matrix of keypoints, use .cpu().numpy() to convert it
                    # keypoints has 17 objects, one for each landmark
                    ear_left = keypoints.data[0][3]
                    ear_right = keypoints.data[0][4]
                    shoulder_left = keypoints.data[0][5]
                    shoulder_right = keypoints.data[0][6]
                    wrist_left = keypoints.data[0][9]
                    wrist_right = keypoints.data[0][10]
                    hand_crop = (
                        ear_left[0] - ear_right[0]
                    )  # hand crop dimensions are relative to the size of the person's face
                    poll_data["people_detected"]["count"] += 1

                    for j, wrist_data in enumerate([wrist_left, wrist_right]):
                        x, y, confidence = wrist_data
                        hand_raised = y < ((shoulder_left[1] + shoulder_right[1]) / 2) + (hand_crop / 2)

                        # process hand if the confidence is high enough and the arm/hand position is "raised" (around or above the shoulder-line)
                        if confidence > GESTURE_THRESHOLD and hand_raised:
                            x_upper, y_upper, x_lower, y_lower = gesture_crop_dimensions(x, y, hand_crop, width, height)
                            wrist_cropped = image.copy()[
                                y_upper:y_lower,
                                x_upper:x_lower,
                            ]
                            wrist_cropped_rgb = cv2.cvtColor(
                                wrist_cropped, cv2.COLOR_BGR2RGB
                            )  # Mediapipe only accepts RGB images, cv2 returns BGR image so we must convert it here
                            top_gesture, hand_landmarks = recognize_gesture(wrist_cropped_rgb)
                            if top_gesture != None:
                                wrist_cropped_bgr = annotate_gesture_and_hand_landmark(
                                    wrist_cropped_rgb, top_gesture, hand_landmarks
                                )  # Mediapipe returns a BGR image, convert it back into RGB
                                cv2.imwrite(f"outputs/person{i}_gesture{j}.jpg", wrist_cropped_bgr)
                                poll_data["gestures_detected"]["count"] += 1
                                poll_data["gestures_detected"]["gestures"].setdefault(top_gesture.category_name, 0)
                                poll_data["gestures_detected"]["gestures"][top_gesture.category_name] += 1

                                # Convert nparray image array to base64 image string, converts BGR to RGB
                                img_byte_arr = io.BytesIO()
                                gesture_img = Image.fromarray(wrist_cropped_bgr.astype("uint8"), "RGB")
                                gesture_img.save(img_byte_arr, format="JPEG")
                                base64_image = b64encode(img_byte_arr.getvalue()).decode("utf-8")
                                poll_data["gestures_detected"]["media"].append(base64_image)

                cv2.imwrite("outputs/output.jpg", pose_annotations)
                img_byte_arr = io.BytesIO()
                pose_img = Image.fromarray(poll_data["people_detected"]["media"].astype("uint8"), "RGB")
                pose_img.save(img_byte_arr, format="JPEG")
                base64_image = b64encode(img_byte_arr.getvalue()).decode("utf-8")
                poll_data["people_detected"]["media"] = base64_image

            #   Process video file
            elif file_extension in video_extensions:
                best_gestures = {}

                # Prepare OpenCV VideoWriter to create a new output MP4 file
                file.save("media.mp4")
                cap = cv2.VideoCapture("media.mp4")
                frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                out = cv2.VideoWriter("outputs/output.mp4", fourcc, 30.0, (frame_width, frame_height))

                # Annotate the video frame by frame
                while cap.isOpened():
                    ret, frame = cap.read()
                    if not ret:
                        break

                    results = model(frame, save=False)
                    results_plotted = results[0].plot()
                    out.write(results_plotted)
                    poll_data["people_detected"]["count"] = 0

                    # each loop represents one person detected (i.e. three people detected, three loops)
                    for i, keypoints in enumerate(
                        results[0].keypoints.cpu().numpy()
                    ):  # .keypoints returns a Tensor matrix of keypoints, use .cpu().numpy() to convert it
                        # keypoints has 17 objects, one for each landmark
                        ear_left = keypoints.data[0][3]
                        ear_right = keypoints.data[0][4]
                        shoulder_left = keypoints.data[0][5]
                        shoulder_right = keypoints.data[0][6]
                        wrist_left = keypoints.data[0][9]
                        wrist_right = keypoints.data[0][10]
                        hand_crop = (
                            ear_left[0] - ear_right[0]
                        )  # hand crop dimensions are relative to the size of the person's face
                        poll_data["people_detected"]["count"] += 1

                    for j, wrist_data in enumerate([wrist_left, wrist_right]):
                        x, y, confidence = wrist_data
                        hand_raised = y < ((shoulder_left[1] + shoulder_right[1]) / 2) + (hand_crop / 2)

                        # process hand if the confidence is high enough and the arm/hand position is "raised" (around or above the shoulder-line)
                        if confidence > GESTURE_THRESHOLD and hand_raised:
                            x_upper, y_upper, x_lower, y_lower = gesture_crop_dimensions(
                                x, y, hand_crop, frame_width, frame_height
                            )
                            wrist_cropped = frame.copy()[
                                y_upper:y_lower,
                                x_upper:x_lower,
                            ]
                            wrist_cropped_rgb = cv2.cvtColor(
                                wrist_cropped, cv2.COLOR_BGR2RGB
                            )  # Mediapipe only accepts RGB images, cv2 returns BGR image so we must convert it here
                            top_gesture, hand_landmarks = recognize_gesture(wrist_cropped_rgb)
                            if top_gesture != None and top_gesture.category_name != "None":
                                wrist_cropped_bgr = annotate_gesture_and_hand_landmark(
                                    wrist_cropped_rgb, top_gesture, hand_landmarks
                                )  # Mediapipe returns a BGR image, convert it back into RGB
                                current_gesture = best_gestures.setdefault(
                                    f"person{i}_gesture{j}", [wrist_cropped_bgr, top_gesture]
                                )
                                if current_gesture[1].score < top_gesture.score:
                                    best_gestures[f"person{i}_gesture{j}"] = [wrist_cropped_bgr, top_gesture]

                with open("outputs/output.mp4", "rb") as video_file:
                    video_data = video_file.read()
                    base64_encoded_data = b64encode(video_data)
                    base64_string = base64_encoded_data.decode("utf-8")
                    poll_data["people_detected"]["media"] = base64_string

                for key, value in best_gestures.items():
                    if value[0].any():
                        cv2.imwrite(f"outputs/{key}.jpg", value[0])
                        poll_data["gestures_detected"]["count"] += 1
                        poll_data["gestures_detected"]["gestures"].setdefault(value[1].category_name, 0)
                        poll_data["gestures_detected"]["gestures"][value[1].category_name] += 1

                        # Convert nparray image array to base64 image string
                        img_byte_arr = io.BytesIO()
                        gesture_img = Image.fromarray(value[0].astype("uint8"), "RGB")
                        gesture_img.save(img_byte_arr, format="JPEG")
                        base64_image = b64encode(img_byte_arr.getvalue()).decode("utf-8")
                        poll_data["gestures_detected"]["media"].append(base64_image)

        json_object = json.dumps(poll_data, indent=4)
        with open("outputs/data.json", "w") as outfile:
            outfile.write(json_object)

        response = Response("200")
        return response

    elif request.method == "GET":
        with open("outputs/data.json", "r") as openfile:
            json_object = json.load(openfile)
            return json_object


@app.route("/videos")
def serve_video():
    range_header = request.headers.get("Range", None)
    if not range_header:
        return send_file("outputs/output.mp4")

    size = os.path.getsize("outputs/output.mp4")
    byte1, byte2 = 0, None

    m = re.search("(\d+)-(\d*)", range_header)
    g = m.groups()

    if g[0]:
        byte1 = int(g[0])
    if g[1]:
        byte2 = int(g[1])

    length = size - byte1
    if byte2 is not None:
        length = byte2 - byte1

    data = None
    with open("outputs/output.mp4", "rb") as f:
        f.seek(byte1)
        data = f.read(length)

    rv = Response(data, 206, mimetype="video/mp4", content_type="video/mp4", direct_passthrough=True)
    rv.headers.add("Content-Range", "bytes {0}-{1}/{2}".format(byte1, byte1 + length - 1, size))

    return rv


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QuimPoll")
    app.run(host="0.0.0.0", port=5000)
