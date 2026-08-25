/*
 * Compatibility validation xApp for the exact E2SM-RC capability advertised
 * by the srsRAN DU: Control Style 2, Action 6 (slice-level PRB quota).
 *
 * This is protocol-emulation evidence, not an over-the-air measurement.
 */

#include "/opt/oranos-stack/flexric/src/xApp/e42_xapp_api.h"
#include "/opt/oranos-stack/flexric/src/sm/rc_sm/ie/ir/ran_param_list.h"
#include "/opt/oranos-stack/flexric/src/sm/rc_sm/ie/ir/ran_param_struct.h"

#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

enum {
  RRM_POLICY_RATIO_LIST = 1,
  RRM_POLICY_RATIO_GROUP = 2,
  RRM_POLICY = 3,
  RRM_POLICY_MEMBER_LIST = 5,
  RRM_POLICY_MEMBER = 6,
  PLMN_IDENTITY = 7,
  S_NSSAI = 8,
  SST = 9,
  SD = 10,
  MIN_PRB_POLICY_RATIO = 11,
  MAX_PRB_POLICY_RATIO = 12,
  DEDICATED_PRB_POLICY_RATIO = 13,
};

static ran_param_struct_t *set_structure(seq_ran_param_t *param, uint32_t id, size_t count)
{
  param->ran_param_id = id;
  param->ran_param_val.type = STRUCTURE_RAN_PARAMETER_VAL_TYPE;
  param->ran_param_val.strct = calloc(1, sizeof(*param->ran_param_val.strct));
  assert(param->ran_param_val.strct != NULL);
  param->ran_param_val.strct->sz_ran_param_struct = count;
  param->ran_param_val.strct->ran_param_struct = calloc(count, sizeof(seq_ran_param_t));
  assert(param->ran_param_val.strct->ran_param_struct != NULL);
  return param->ran_param_val.strct;
}

static ran_param_struct_t *set_single_list(seq_ran_param_t *param, uint32_t id, size_t count)
{
  param->ran_param_id = id;
  param->ran_param_val.type = LIST_RAN_PARAMETER_VAL_TYPE;
  param->ran_param_val.lst = calloc(1, sizeof(*param->ran_param_val.lst));
  assert(param->ran_param_val.lst != NULL);
  param->ran_param_val.lst->sz_lst_ran_param = 1;
  param->ran_param_val.lst->lst_ran_param = calloc(1, sizeof(lst_ran_param_t));
  assert(param->ran_param_val.lst->lst_ran_param != NULL);
  ran_param_struct_t *strct = &param->ran_param_val.lst->lst_ran_param[0].ran_param_struct;
  strct->sz_ran_param_struct = count;
  strct->ran_param_struct = calloc(count, sizeof(seq_ran_param_t));
  assert(strct->ran_param_struct != NULL);
  return strct;
}

static void set_integer(seq_ran_param_t *param, uint32_t id, int64_t value)
{
  param->ran_param_id = id;
  param->ran_param_val.type = ELEMENT_KEY_FLAG_FALSE_RAN_PARAMETER_VAL_TYPE;
  param->ran_param_val.flag_false = calloc(1, sizeof(*param->ran_param_val.flag_false));
  assert(param->ran_param_val.flag_false != NULL);
  param->ran_param_val.flag_false->type = INTEGER_RAN_PARAMETER_VALUE;
  param->ran_param_val.flag_false->int_ran = value;
}

static void set_octets(seq_ran_param_t *param, uint32_t id, const uint8_t *bytes, size_t len)
{
  param->ran_param_id = id;
  param->ran_param_val.type = ELEMENT_KEY_FLAG_FALSE_RAN_PARAMETER_VAL_TYPE;
  param->ran_param_val.flag_false = calloc(1, sizeof(*param->ran_param_val.flag_false));
  assert(param->ran_param_val.flag_false != NULL);
  param->ran_param_val.flag_false->type = OCTET_STRING_RAN_PARAMETER_VALUE;
  param->ran_param_val.flag_false->octet_str_ran.len = len;
  param->ran_param_val.flag_false->octet_str_ran.buf = calloc(len, sizeof(uint8_t));
  assert(param->ran_param_val.flag_false->octet_str_ran.buf != NULL);
  memcpy(param->ran_param_val.flag_false->octet_str_ran.buf, bytes, len);
}

static rc_ctrl_req_data_t make_slice_control(uint32_t gnb_cu_ue_f1ap_id,
                                             int64_t min_prb, int64_t max_prb,
                                             int64_t dedicated_prb)
{
  rc_ctrl_req_data_t req = {0};
  req.hdr.format = FORMAT_1_E2SM_RC_CTRL_HDR;
  req.hdr.frmt_1.ue_id.type = GNB_DU_UE_ID_E2SM;
  req.hdr.frmt_1.ue_id.gnb_du.gnb_cu_ue_f1ap = gnb_cu_ue_f1ap_id;
  req.hdr.frmt_1.ue_id.gnb_du.ran_ue_id = NULL;
  req.hdr.frmt_1.ric_style_type = 2;
  req.hdr.frmt_1.ctrl_act_id = 6;
  req.hdr.frmt_1.ric_ctrl_decision = NULL;

  req.msg.format = FORMAT_1_E2SM_RC_CTRL_MSG;
  req.msg.frmt_1.sz_ran_param = 1;
  req.msg.frmt_1.ran_param = calloc(1, sizeof(seq_ran_param_t));
  assert(req.msg.frmt_1.ran_param != NULL);

  /* 1: RRM Policy Ratio List -> 2: RRM Policy Ratio Group. */
  ran_param_struct_t *ratio_list =
      set_single_list(&req.msg.frmt_1.ran_param[0], RRM_POLICY_RATIO_LIST, 1);
  ran_param_struct_t *ratio_group =
      set_structure(&ratio_list->ran_param_struct[0], RRM_POLICY_RATIO_GROUP, 4);

  /* 3: RRM Policy -> 5: Member List -> 6: Member. */
  ran_param_struct_t *policy = set_structure(&ratio_group->ran_param_struct[0], RRM_POLICY, 1);
  ran_param_struct_t *member_list =
      set_single_list(&policy->ran_param_struct[0], RRM_POLICY_MEMBER_LIST, 1);
  ran_param_struct_t *member =
      set_structure(&member_list->ran_param_struct[0], RRM_POLICY_MEMBER, 2);

  /* PLMN 999-70 in 3GPP BCD, followed by S-NSSAI SST=1, SD=1. */
  const uint8_t plmn[] = {0x99, 0xf9, 0x07};
  const uint8_t sst[] = {0x01};
  const uint8_t sd[] = {0x00, 0x00, 0x01};
  set_octets(&member->ran_param_struct[0], PLMN_IDENTITY, plmn, sizeof(plmn));
  ran_param_struct_t *snssai = set_structure(&member->ran_param_struct[1], S_NSSAI, 2);
  set_octets(&snssai->ran_param_struct[0], SST, sst, sizeof(sst));
  set_octets(&snssai->ran_param_struct[1], SD, sd, sizeof(sd));

  set_integer(&ratio_group->ran_param_struct[1], MIN_PRB_POLICY_RATIO, min_prb);
  set_integer(&ratio_group->ran_param_struct[2], MAX_PRB_POLICY_RATIO, max_prb);
  set_integer(&ratio_group->ran_param_struct[3], DEDICATED_PRB_POLICY_RATIO, dedicated_prb);
  return req;
}

static int capability_is_present(const e2_node_connected_xapp_t *node)
{
  for (size_t i = 0; i < node->len_rf; ++i) {
    if (node->rf[i].id != 3 || node->rf[i].defn.type != RC_RAN_FUNC_DEF_E ||
        node->rf[i].defn.rc.ctrl == NULL) {
      continue;
    }
    const ran_func_def_ctrl_t *ctrl = node->rf[i].defn.rc.ctrl;
    for (size_t j = 0; j < ctrl->sz_seq_ctrl_style; ++j) {
      const seq_ctrl_style_t *style = &ctrl->seq_ctrl_style[j];
      if (style->style_type != 2) {
        continue;
      }
      for (size_t k = 0; k < style->sz_seq_ctrl_act; ++k) {
        if (style->seq_ctrl_act[k].id == 6) {
          return 1;
        }
      }
    }
  }
  return 0;
}

int main(int argc, char **argv)
{
  /* Positional args before the FlexRIC flags: ue_id [min max dedicated]. */
  uint32_t ue_id = 0;
  int64_t min_prb = 10, max_prb = 90, dedicated_prb = 80;
  int64_t *slots[3] = {&min_prb, &max_prb, &dedicated_prb};
  int taken = 0;
  if (argc > 1 && argv[1][0] != '-') {
    ue_id = (uint32_t)strtoul(argv[1], NULL, 10);
    --argc;
    ++argv;
  }
  while (taken < 3 && argc > 1 && argv[1][0] != '-') {
    *slots[taken++] = (int64_t)strtol(argv[1], NULL, 10);
    --argc;
    ++argv;
  }

  fr_args_t args = init_fr_args(argc, argv);
  init_xapp_api(&args);
  sleep(1);

  e2_node_arr_xapp_t nodes = e2_nodes_xapp_api();
  assert(nodes.len > 0);
  assert(capability_is_present(&nodes.n[0]) &&
         "srsRAN did not advertise E2SM-RC Style 2 / Action 6");
  printf("[RC-SLICE] capability confirmed: style=2 action=6 ue=%u\n", ue_id);

  printf("[RC-SLICE] sending quota min=%lld max=%lld dedicated=%lld\n",
         (long long)min_prb, (long long)max_prb, (long long)dedicated_prb);
  rc_ctrl_req_data_t req = make_slice_control(ue_id, min_prb, max_prb, dedicated_prb);
  sm_ans_xapp_t ans = control_sm_xapp_api(&nodes.n[0].id, 3, &req);
  printf("[RC-SLICE] synchronous control response received: %s\n",
         ans.success ? "true" : "false");

  free_rc_ctrl_req_data(&req);
  free_e2_node_arr_xapp(&nodes);
  while (!try_stop_xapp_api()) {
    usleep(1000);
  }
  printf("[RC-SLICE] validation xApp completed\n");
  return ans.success ? 0 : 2;
}
