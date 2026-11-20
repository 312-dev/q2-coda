// SPDX-License-Identifier: GPL-2.0
/* Never loaded. The kernel structures' sizes and the offsets of the fields f_uac1.c and u_audio.c touch, as this
 * tree's configuration lays them out, each a q2_off_* constant that tools/uac1_module/uac1_build.py reads back and checks
 * against the stock kernel (docs/iphone-mode.md#the-uac1-module). */
#include <linux/module.h>
#include <linux/usb/composite.h>
#include <linux/usb/gadget.h>
#include <sound/core.h>
#include <sound/pcm.h>

#define OFF(name, type, field) const unsigned int q2_off_##name __used = offsetof(type, field)
#define SIZE(name, type) const unsigned int q2_size_##name __used = sizeof(type)

OFF(req_buf, struct usb_request, buf);
OFF(req_length, struct usb_request, length);
OFF(req_complete, struct usb_request, complete);
OFF(req_context, struct usb_request, context);
OFF(req_status, struct usb_request, status);
OFF(req_actual, struct usb_request, actual);
OFF(ep_name, struct usb_ep, name);
OFF(ep_ops, struct usb_ep, ops);
OFF(ep_desc, struct usb_ep, desc);
OFF(gadget_ep0, struct usb_gadget, ep0);
OFF(gadget_speed, struct usb_gadget, speed);
OFF(gadget_dev, struct usb_gadget, dev);
OFF(gadget_max_speed, struct usb_gadget, max_speed);
OFF(gadget_state, struct usb_gadget, state);
OFF(dev_driver_data, struct device, driver_data);
OFF(cdev_gadget, struct usb_composite_dev, gadget);
OFF(cdev_req, struct usb_composite_dev, req);
OFF(cdev_config, struct usb_composite_dev, config);
OFF(config_cdev, struct usb_configuration, cdev);
OFF(func_name, struct usb_function, name);
OFF(func_strings, struct usb_function, strings);
OFF(func_fs_descriptors, struct usb_function, fs_descriptors);
OFF(func_hs_descriptors, struct usb_function, hs_descriptors);
OFF(func_ss_descriptors, struct usb_function, ss_descriptors);
OFF(func_ssp_descriptors, struct usb_function, ssp_descriptors);
OFF(func_config, struct usb_function, config);
OFF(func_fi, struct usb_function, fi);
OFF(func_bind, struct usb_function, bind);
OFF(func_unbind, struct usb_function, unbind);
OFF(func_free_func, struct usb_function, free_func);
OFF(func_set_alt, struct usb_function, set_alt);
OFF(func_get_alt, struct usb_function, get_alt);
OFF(func_disable, struct usb_function, disable);
OFF(func_setup, struct usb_function, setup);
OFF(func_list, struct usb_function, list);
SIZE(usb_function, struct usb_function);
OFF(fi_group, struct usb_function_instance, group);
OFF(fi_fd, struct usb_function_instance, fd);
OFF(fi_free_func_inst, struct usb_function_instance, free_func_inst);
SIZE(usb_function_instance, struct usb_function_instance);
SIZE(config_group, struct config_group);
OFF(fd_name, struct usb_function_driver, name);
OFF(fd_alloc_inst, struct usb_function_driver, alloc_inst);
OFF(fd_alloc_func, struct usb_function_driver, alloc_func);
OFF(card_id, struct snd_card, id);
OFF(card_driver, struct snd_card, driver);
OFF(card_shortname, struct snd_card, shortname);
OFF(card_longname, struct snd_card, longname);
OFF(card_private_data, struct snd_card, private_data);
OFF(card_dev, struct snd_card, dev);
OFF(pcm_card, struct snd_pcm, card);
OFF(pcm_name, struct snd_pcm, name);
OFF(pcm_streams, struct snd_pcm, streams);
OFF(pcm_private_data, struct snd_pcm, private_data);
SIZE(snd_pcm_str, struct snd_pcm_str);
OFF(sub_pcm, struct snd_pcm_substream, pcm);
OFF(sub_stream, struct snd_pcm_substream, stream);
OFF(sub_private_data, struct snd_pcm_substream, private_data);
OFF(sub_ops, struct snd_pcm_substream, ops);
OFF(sub_runtime, struct snd_pcm_substream, runtime);
OFF(rt_hw, struct snd_pcm_runtime, hw);
OFF(rt_frame_bits, struct snd_pcm_runtime, frame_bits);
OFF(rt_buffer_size, struct snd_pcm_runtime, buffer_size);
OFF(rt_dma_area, struct snd_pcm_runtime, dma_area);
OFF(rt_dma_bytes, struct snd_pcm_runtime, dma_bytes);
SIZE(snd_pcm_hardware, struct snd_pcm_hardware);
SIZE(spinlock, spinlock_t);
SIZE(mutex, struct mutex);
SIZE(device, struct device);
OFF(dev_id, struct device, id);
OFF(module_init, struct module, init);
OFF(module_exit, struct module, exit);
SIZE(module, struct module);
/* the page size the module's code was compiled for: u_audio.c's PCM limits are multiples of it */
const unsigned int q2_size_page __used = PAGE_SIZE;

/* ep->maxpacket is a bit-field: uac1_build.py reads the load this makes */
unsigned int q2_maxpacket(const struct usb_ep *ep) { return ep->maxpacket; }
EXPORT_SYMBOL_GPL(q2_maxpacket);

MODULE_LICENSE("GPL");
