/** @odoo-module **/

import { Component, onMounted, onWillDestroy, useRef, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { BinaryField, binaryField } from "@web/views/fields/binary/binary_field";

export class BillCameraDialog extends Component {
    static template = "wholesale_shop_pos.BillCameraDialog";
    static components = { Dialog };
    static props = { close: Function, onCaptured: Function };

    setup() {
        this.video = useRef("video");
        this.state = useState({
            ready: false, saving: false, error: "",
            torchAvailable: false, torchOn: false, torchBusy: false, torchError: "",
        });
        this.title = _t("Take a photo");
        onMounted(() => this.startCamera());
        onWillDestroy(() => {
            this.destroyed = true;
            this.stopCamera();
        });
    }

    stopCamera() {
        this.stream?.getTracks().forEach((track) => track.stop());
        this.stream = null;
        this.videoTrack = null;
        if (!this.destroyed) {
            this.state.ready = false;
            this.state.torchAvailable = false;
            this.state.torchOn = false;
        }
    }

    async toggleTorch() {
        const track = this.videoTrack;
        if (!track || !this.state.torchAvailable || this.state.torchBusy || this.state.saving) return;
        const enabled = !this.state.torchOn;
        this.state.torchBusy = true;
        this.state.torchError = "";
        try {
            await track.applyConstraints({ advanced: [{ torch: enabled }] });
            if (!this.destroyed && this.videoTrack === track) this.state.torchOn = enabled;
        } catch {
            if (!this.destroyed) {
                this.state.torchError = _t("Could not switch the flashlight. Your device or browser may not support camera lighting.");
            }
        } finally {
            if (!this.destroyed) this.state.torchBusy = false;
        }
    }

    async startCamera() {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: { ideal: "environment" }, width: { ideal: 2560 }, height: { ideal: 1920 } },
                audio: false,
            });
            if (this.destroyed) {
                stream.getTracks().forEach((track) => track.stop());
                return;
            }
            this.stream = stream;
            this.videoTrack = stream.getVideoTracks()[0];
            try {
                this.state.torchAvailable = Boolean(
                    this.videoTrack?.getCapabilities?.().torch && this.videoTrack.applyConstraints
                );
                this.state.torchOn = Boolean(this.videoTrack?.getSettings?.().torch);
            } catch {
                this.state.torchAvailable = false;
            }
            this.video.el.srcObject = stream;
            await this.video.el.play();
        } catch (error) {
            this.stopCamera();
            if (!this.destroyed) {
                this.state.error = _t("Could not open the camera. Allow camera access in your browser and check that your camera is connected and available.");
            }
        }
    }

    onReady() {
        this.state.ready = true;
    }

    async capture() {
        if (!this.state.ready || this.state.saving || this.state.torchBusy) return;
        const video = this.video.el;
        if (!video.videoWidth || !video.videoHeight) return;
        this.state.saving = true;
        try {
            const canvas = document.createElement("canvas");
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            canvas.getContext("2d").drawImage(video, 0, 0);
            await this.props.onCaptured({
                data: canvas.toDataURL("image/jpeg", 0.95).split(",")[1],
                name: `bill-photo-${Date.now()}.jpg`,
            });
            this.props.close();
        } catch (error) {
            this.state.error = _t("Could not save the photo. Please try again.");
        } finally {
            this.state.saving = false;
        }
    }
}

export class BillCameraField extends BinaryField {
    static template = "wholesale_shop_pos.BillCameraField";

    setup() {
        super.setup();
        this.dialog = useService("dialog");
        this.cameraInput = useRef("cameraInput");
    }

    takePhoto() {
        if (this.props.readonly) return;
        // Native capture also works on phones served over a local HTTP connection.
        if (!navigator.mediaDevices?.getUserMedia) {
            this.cameraInput.el.click();
            return;
        }
        this.dialog.add(BillCameraDialog, { onCaptured: (photo) => this.update(photo) });
    }

    async onCameraFile(event) {
        const input = event.target;
        const file = input.files?.[0];
        if (!file || this.props.readonly) return;
        try {
            const data = await new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(reader.result.split(",")[1]);
                reader.onerror = reject;
                reader.readAsDataURL(file);
            });
            await this.update({ data, name: file.name });
        } catch (error) {
            this.notification.add(_t("Could not load the photo. Please try again."), { type: "danger" });
        } finally {
            input.value = "";
        }
    }
}

registry.category("fields").add("bill_camera", { ...binaryField, component: BillCameraField });
